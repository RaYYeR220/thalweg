import * as path from 'path';
import { Duration, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';

export const REPO_ROOT = path.join(__dirname, '..', '..');
export const SERVICES_DIR = path.join(REPO_ROOT, 'services');
export const STATEMACHINE_DIR = path.join(REPO_ROOT, 'statemachine');
export const FUNCTIONS_DIR = path.join(__dirname, '..', 'functions');

export const NAMES = {
  activity: 'thalweg-agent',
  stateMachine: 'thalweg-gauging',
  tables: {
    stations: 'thalweg-stations',
    gaugings: 'thalweg-gaugings',
    runs: 'thalweg-runs',
    warnings: 'thalweg-warnings',
    budget: 'thalweg-budget',
  },
  params: {
    veniceKey: '/thalweg/venice_api_key',
    reviewerPasscode: '/thalweg/reviewer_passcode',
    serviceToken: '/thalweg/mcp_token',
    notifyEmail: '/thalweg/notify_email',
  },
  metricsNamespace: 'Thalweg',
  /** Venice model ids: judge-triggered LLM runs, scheduled replays, and what POST /runs accepts. */
  models: {
    judge: 'claude-sonnet-5-5',
    replay: 'claude-haiku-5-5',
    allowed: ['claude-sonnet-5-5', 'claude-haiku-5-5'],
  },
  replaySchedule: 'thalweg-replay',
  dashboard: 'thalweg',
  budget: 'thalweg',
} as const;

export const LOG_RETENTION = logs.RetentionDays.TWO_WEEKS;

const servicesCode = new WeakMap<Stack, lambda.AssetCode>();

/** One asset per stack for every Python handler: services/ minus its tests. */
export function servicesAsset(scope: Construct): lambda.AssetCode {
  const stack = Stack.of(scope);
  let code = servicesCode.get(stack);
  if (!code) {
    code = lambda.Code.fromAsset(SERVICES_DIR, {
      exclude: ['tests', 'tests/**', '**/__pycache__', '**/*.pyc', '.pytest_cache', '**/.pytest_cache'],
    });
    servicesCode.set(stack, code);
  }
  return code;
}

export interface PyFunctionProps {
  readonly functionName: string;
  readonly handler: string;
  readonly description: string;
  readonly environment: Record<string, string>;
  readonly memorySize?: number;
  readonly timeout?: Duration;
}

/** Python 3.12 on arm64 with X-Ray and a 14-day log group. */
export function pyFunction(scope: Construct, id: string, props: PyFunctionProps): lambda.Function {
  const logGroup = new logs.LogGroup(scope, `${id}Logs`, {
    logGroupName: `/aws/lambda/${props.functionName}`,
    retention: LOG_RETENTION,
    removalPolicy: RemovalPolicy.DESTROY,
  });
  return new lambda.Function(scope, id, {
    functionName: props.functionName,
    description: props.description,
    runtime: lambda.Runtime.PYTHON_3_12,
    architecture: lambda.Architecture.ARM_64,
    handler: props.handler,
    code: servicesAsset(scope),
    memorySize: props.memorySize ?? 256,
    timeout: props.timeout ?? Duration.seconds(30),
    environment: props.environment,
    tracing: lambda.Tracing.ACTIVE,
    logGroup,
  });
}
