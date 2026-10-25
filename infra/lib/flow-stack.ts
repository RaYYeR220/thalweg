import * as fs from 'fs';
import * as path from 'path';
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventTargets from 'aws-cdk-lib/aws-events-targets';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as schedulerTargets from 'aws-cdk-lib/aws-scheduler-targets';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import { Construct } from 'constructs';
import { LOG_RETENTION, NAMES, STATEMACHINE_DIR, pyFunction } from './common';
import { DataStack } from './data-stack';

export interface FlowStackProps extends StackProps {
  readonly data: DataStack;
  /** Run the replay schedule (off until the Dart archive and its manifest are uploaded). */
  readonly replayEnabled?: boolean;
}

/**
 * The gauging workflow: the `thalweg-agent` activity the Graviton worker polls, the
 * `thalweg-gauging` state machine and its task Lambdas, the ingest trigger and the replay schedule.
 */
export class FlowStack extends Stack {
  readonly activity: sfn.Activity;
  readonly stateMachine: sfn.StateMachine;
  readonly functions: lambda.Function[] = [];
  readonly replaySchedule: scheduler.Schedule;

  constructor(scope: Construct, id: string, props: FlowStackProps) {
    super(scope, id, props);
    const { data } = props;

    this.activity = new sfn.Activity(this, 'Agent', { activityName: NAMES.activity });

    const env = {
      TABLE_STATIONS: data.stations.tableName,
      TABLE_GAUGINGS: data.gaugings.tableName,
      TABLE_RUNS: data.runs.tableName,
      TABLE_WARNINGS: data.warnings.tableName,
      TABLE_BUDGET: data.budget.tableName,
      INGEST_BUCKET: data.ingest.bucketName,
      ARTIFACTS_BUCKET: data.artifacts.bucketName,
      METRICS_NAMESPACE: NAMES.metricsNamespace,
    };
    const fn = (id: string, name: string, handler: string, description: string, extra: Record<string, string> = {}, timeout?: Duration) => {
      const f = pyFunction(this, id, { functionName: name, handler, description, environment: { ...env, ...extra }, timeout });
      this.functions.push(f);
      return f;
    };

    // --- state machine tasks --------------------------------------------------
    const preflight = fn('Preflight', 'thalweg-preflight', 'workflow.preflight.handler', 'Checks station, clip and sidecar; opens the run.');
    data.stations.grantReadData(preflight);
    data.runs.grantWriteData(preflight);
    data.ingest.grantRead(preflight, 'clips/*');

    const failClosed = fn('FailClosed', 'thalweg-fail-closed', 'workflow.fail_closed.handler', 'Ends a run in HOLD (fail_closed:<error>).');
    data.runs.grantWriteData(failClosed);
    data.stations.grantWriteData(failClosed);
    data.gaugings.grantWriteData(failClosed);

    const persist = fn('Persist', 'thalweg-persist', 'workflow.persist.handler', 'Records the decision, the gauging on PUBLISH, station latest fields and metrics.');
    data.runs.grantWriteData(persist);
    data.gaugings.grantWriteData(persist);
    data.stations.grantWriteData(persist);

    const awaitSignature = fn('AwaitSignature', 'thalweg-await-signature', 'workflow.signature.await_handler', 'Stores a warning draft with its task token.');
    data.warnings.grantWriteData(awaitSignature);

    const issueWarning = fn('IssueWarning', 'thalweg-issue-warning', 'workflow.signature.issue_handler', 'Marks a signed warning SIGNED and republishes public/warnings.json.');
    data.warnings.grantReadWriteData(issueWarning);
    data.artifacts.grantPut(issueWarning, 'public/warnings.json');

    const closeWarning = fn('CloseWarning', 'thalweg-close-warning', 'workflow.signature.close_handler', 'Marks a warning REJECTED or EXPIRED.');
    data.warnings.grantWriteData(closeWarning);

    // --- state machine ----------------------------------------------------------
    const definition = fs.readFileSync(path.join(STATEMACHINE_DIR, 'gauging.asl.json'), 'utf8');
    const smLogs = new logs.LogGroup(this, 'GaugingLogs', {
      logGroupName: `/aws/vendedlogs/states/${NAMES.stateMachine}`,
      retention: LOG_RETENTION,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.stateMachine = new sfn.StateMachine(this, 'Gauging', {
      stateMachineName: NAMES.stateMachine,
      stateMachineType: sfn.StateMachineType.STANDARD,
      definitionBody: sfn.DefinitionBody.fromString(definition),
      definitionSubstitutions: {
        PreflightFunctionArn: preflight.functionArn,
        AgentActivityArn: this.activity.activityArn,
        PersistFunctionArn: persist.functionArn,
        AwaitSignatureFunctionArn: awaitSignature.functionArn,
        IssueWarningFunctionArn: issueWarning.functionArn,
        CloseWarningFunctionArn: closeWarning.functionArn,
        FailClosedFunctionArn: failClosed.functionArn,
      },
      tracingEnabled: true,
      logs: { destination: smLogs, level: sfn.LogLevel.ERROR, includeExecutionData: false },
    });
    for (const f of [preflight, persist, awaitSignature, issueWarning, closeWarning, failClosed]) {
      f.grantInvoke(this.stateMachine);
    }

    // --- ingest trigger -----------------------------------------------------------
    const startRun = fn('StartRun', 'thalweg-start-run', 'workflow.start_run.handler', 'Starts a gauging when a clip sidecar lands in the ingest bucket.', {
      STATE_MACHINE_ARN: this.stateMachine.stateMachineArn,
    });
    data.ingest.grantRead(startRun, 'clips/*');
    data.runs.grantReadWriteData(startRun);
    this.stateMachine.grantStartExecution(startRun);

    new events.Rule(this, 'ClipArrived', {
      ruleName: 'thalweg-clip-arrived',
      description: 'A clip sidecar (clips/<station>/<stamp>.json) was written to the ingest bucket.',
      eventPattern: {
        source: ['aws.s3'],
        detailType: ['Object Created'],
        detail: {
          bucket: { name: [data.ingest.bucketName] },
          object: { key: events.Match.wildcard('clips/*.json') },
        },
      },
      targets: [new eventTargets.LambdaFunction(startRun, { retryAttempts: 4, maxEventAge: Duration.hours(1) })],
    });

    // --- replay -------------------------------------------------------------------
    const replay = fn('Replay', 'thalweg-replay', 'workflow.replay.handler', 'Feeds the next archived Storm Callum clip into the ingest bucket.', {
      REPLAY_MANIFEST_KEY: 'data/dart/replay-manifest.json',
      REPLAY_MODEL: NAMES.models.replay,
    }, Duration.seconds(120));
    data.artifacts.grantRead(replay, 'data/*');
    data.ingest.grantPut(replay, 'clips/*');
    data.stations.grantReadWriteData(replay);

    this.replaySchedule = new scheduler.Schedule(this, 'ReplaySchedule', {
      scheduleName: NAMES.replaySchedule,
      description: 'Keeps the River Dart station gauging: one archived clip every 15 minutes.',
      schedule: scheduler.ScheduleExpression.rate(Duration.minutes(15)),
      target: new schedulerTargets.LambdaInvoke(replay, { retryAttempts: 0 }),
      // Switched on (-c replay=enabled) once the Dart archive and its manifest are uploaded.
      enabled: props.replayEnabled ?? false,
    });

    new CfnOutput(this, 'ActivityArn', { value: this.activity.activityArn });
    new CfnOutput(this, 'StateMachineArn', { value: this.stateMachine.stateMachineArn });
    new CfnOutput(this, 'ReplayScheduleName', { value: NAMES.replaySchedule });
  }
}
