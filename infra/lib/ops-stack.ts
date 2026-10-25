import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { ApiStack } from './api-stack';
import { NAMES } from './common';
import { FlowStack } from './flow-stack';

export interface OpsStackProps extends StackProps {
  readonly flow: FlowStack;
  readonly api: ApiStack;
  readonly monthlyBudgetUsd: number;
}

/** Dashboard `thalweg`, the failed-execution and silent-worker alarms, and the $ budget. */
export class OpsStack extends Stack {
  constructor(scope: Construct, id: string, props: OpsStackProps) {
    super(scope, id, props);
    const { flow, api } = props;
    const ns = NAMES.metricsNamespace;
    const smArn = flow.stateMachine.stateMachineArn;
    const activityArn = flow.activity.activityArn;
    // Budget and alarm mail goes to the address in SSM (String) /thalweg/notify_email,
    // resolved at deploy time so it never lives in the repository.
    const notifyEmail = ssm.StringParameter.valueForStringParameter(this, NAMES.params.notifyEmail);
    const fiveMin = Duration.minutes(5);
    const hour = Duration.hours(1);

    const decision = (kind: string, color: string) =>
      new cloudwatch.Metric({ namespace: ns, metricName: 'Decision', dimensionsMap: { kind }, statistic: 'Sum', period: hour, label: kind, color });
    const states = (metricName: string, color?: string) =>
      new cloudwatch.Metric({ namespace: 'AWS/States', metricName, dimensionsMap: { StateMachineArn: smArn }, statistic: 'Sum', period: fiveMin, color });
    const activity = (metricName: string) =>
      new cloudwatch.Metric({ namespace: 'AWS/States', metricName, dimensionsMap: { ActivityArn: activityArn }, statistic: 'Sum', period: fiveMin });
    const apiMetric = (metricName: string, color: string, apiId = api.api.apiId, label = metricName) =>
      new cloudwatch.Metric({ namespace: 'AWS/ApiGateway', metricName, dimensionsMap: { ApiId: apiId }, statistic: 'Sum', period: fiveMin, color, label });

    const failed = states('ExecutionsFailed', '#d62728');
    const topic = new sns.Topic(this, 'Alarms', { topicName: 'thalweg-alarms', displayName: 'Thalweg alarms' });
    topic.addSubscription(new subs.EmailSubscription(notifyEmail));

    const failedAlarm = new cloudwatch.Alarm(this, 'ExecutionsFailedAlarm', {
      alarmName: 'thalweg-executions-failed',
      alarmDescription: 'A gauging run failed closed or a signature step failed (thalweg-gauging ExecutionsFailed >= 1 in 5 min).',
      metric: failed,
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    failedAlarm.addAlarmAction(new cwActions.SnsAction(topic));

    const silent = new cloudwatch.MathExpression({
      expression: 'FILL(t, 0) + FILL(h, 0)',
      usingMetrics: { t: activity('ActivitiesTimedOut'), h: activity('ActivitiesHeartbeatTimedOut') },
      period: fiveMin,
      label: 'agent tasks timed out',
    });
    const silentAlarm = new cloudwatch.Alarm(this, 'WorkerSilentAlarm', {
      alarmName: 'thalweg-worker-silent',
      alarmDescription: 'The Graviton worker missed a heartbeat or never picked up an agent task.',
      metric: silent,
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    silentAlarm.addAlarmAction(new cwActions.SnsAction(topic));

    const lambdaErrors = [...flow.functions, api.handler, api.webHandler].map(
      (f) => new cloudwatch.Metric({ namespace: 'AWS/Lambda', metricName: 'Errors', dimensionsMap: { FunctionName: f.functionName }, statistic: 'Sum', period: fiveMin, label: f.functionName }),
    );

    const dashboard = new cloudwatch.Dashboard(this, 'Dashboard', {
      dashboardName: NAMES.dashboard,
      defaultInterval: Duration.days(1),
    });
    dashboard.addWidgets(
      new cloudwatch.TextWidget({
        markdown: '## Thalweg\nDecisions come from the agent on the Graviton worker; a run that fails before a decision is persisted is HOLD (fail closed).',
        width: 24,
        height: 2,
      }),
    );
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'Decisions by kind (per hour)',
        left: [decision('PUBLISH', '#2ca02c'), decision('HOLD', '#ff7f0e'), decision('ESCALATE', '#9467bd')],
        stacked: true,
        width: 12,
      }),
      new cloudwatch.GraphWidget({
        title: 'Gauging latency, clip to decision (s)',
        left: ['p50', 'p90', 'Maximum'].map(
          (stat) => new cloudwatch.Metric({ namespace: ns, metricName: 'GaugingLatency', statistic: stat, period: hour, label: stat }),
        ),
        width: 12,
      }),
    );
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'LLM cost (USD per day)',
        left: [new cloudwatch.Metric({ namespace: ns, metricName: 'LLMCostUSD', statistic: 'Sum', period: Duration.days(1), label: 'USD' })],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'Policy denials (per hour)',
        left: [new cloudwatch.Metric({ namespace: ns, metricName: 'PolicyDenials', statistic: 'Sum', period: hour, label: 'denied tool calls' })],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'Workflow executions',
        left: [states('ExecutionsStarted', '#1f77b4'), states('ExecutionsSucceeded', '#2ca02c'), failed, states('ExecutionsTimedOut', '#8c564b')],
        width: 8,
      }),
    );
    dashboard.addWidgets(
      new cloudwatch.GraphWidget({
        title: 'API requests and errors',
        left: [apiMetric('Count', '#1f77b4', api.api.apiId, 'api requests'), apiMetric('Count', '#17becf', api.site.apiId, 'site requests')],
        right: [
          apiMetric('4xx', '#ff7f0e', api.api.apiId, 'api 4xx'),
          apiMetric('5xx', '#d62728', api.api.apiId, 'api 5xx'),
          apiMetric('4xx', '#bcbd22', api.site.apiId, 'site 4xx'),
          apiMetric('5xx', '#e377c2', api.site.apiId, 'site 5xx'),
        ],
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'Agent activity (worker)',
        left: ['ActivitiesScheduled', 'ActivitiesStarted', 'ActivitiesSucceeded', 'ActivitiesFailed', 'ActivitiesTimedOut', 'ActivitiesHeartbeatTimedOut'].map(activity),
        width: 8,
      }),
      new cloudwatch.GraphWidget({
        title: 'Lambda errors',
        left: lambdaErrors,
        width: 8,
      }),
    );
    dashboard.addWidgets(
      new cloudwatch.AlarmStatusWidget({ title: 'Alarms', alarms: [failedAlarm, silentAlarm], width: 24, height: 3 }),
    );

    new budgets.CfnBudget(this, 'Budget', {
      budget: {
        budgetName: NAMES.budget,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: props.monthlyBudgetUsd, unit: 'USD' },
        costFilters: { TagKeyValue: ['user:project$thalweg'] },
      },
      notificationsWithSubscribers: [
        {
          notification: {
            notificationType: 'ACTUAL',
            comparisonOperator: 'GREATER_THAN',
            threshold: 80,
            thresholdType: 'PERCENTAGE',
          },
          subscribers: [{ subscriptionType: 'EMAIL', address: notifyEmail }],
        },
      ],
      resourceTags: [{ key: 'project', value: 'thalweg' }],
    });

    new CfnOutput(this, 'DashboardUrl', {
      value: `https://${this.region}.console.aws.amazon.com/cloudwatch/home?region=${this.region}#dashboards:name=${NAMES.dashboard}`,
    });
    new CfnOutput(this, 'AlarmTopicArn', { value: topic.topicArn });
  }
}
