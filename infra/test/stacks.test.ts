import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { App, Tags } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack } from '../lib/api-stack';
import { DataStack } from '../lib/data-stack';
import { FlowStack } from '../lib/flow-stack';
import { OpsStack } from '../lib/ops-stack';
import { WebStack } from '../lib/web-stack';

const env = { account: '123456789012', region: 'us-east-1' };
const app = new App();
Tags.of(app).add('project', 'thalweg');
const data = new DataStack(app, 'ThalwegData', { env });
const flow = new FlowStack(app, 'ThalwegFlow', { env, data });
const api = new ApiStack(app, 'ThalwegApi', { env, data, flow });
const web = new WebStack(app, 'ThalwegWeb', { env, data, api });
const ops = new OpsStack(app, 'ThalwegOps', { env, flow, api, monthlyBudgetUsd: 25 });
const t = {
  data: Template.fromStack(data),
  flow: Template.fromStack(flow),
  api: Template.fromStack(api),
  web: Template.fromStack(web),
  ops: Template.fromStack(ops),
};
const all = Object.values(t);

test('buckets are private, SSE-S3, named per account', () => {
  for (const name of ['thalweg-ingest-123456789012', 'thalweg-artifacts-123456789012', 'thalweg-web-123456789012']) {
    t.data.hasResourceProperties('AWS::S3::Bucket', {
      BucketName: name,
      PublicAccessBlockConfiguration: { BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true },
      BucketEncryption: { ServerSideEncryptionConfiguration: [{ ServerSideEncryptionByDefault: { SSEAlgorithm: 'AES256' } }] },
    });
  }
  t.web.resourceCountIs('AWS::S3::Bucket', 0);
  t.data.hasResourceProperties('AWS::S3::Bucket', {
    BucketName: 'thalweg-ingest-123456789012',
    NotificationConfiguration: { EventBridgeConfiguration: { EventBridgeEnabled: true } },
  });
});

test('tables, keys, indexes and removal policies', () => {
  const table = (name: string, keys: object[], policy: 'Retain' | 'Delete') =>
    t.data.hasResource('AWS::DynamoDB::Table', {
      DeletionPolicy: policy,
      Properties: Match.objectLike({
        TableName: name,
        KeySchema: keys,
        BillingMode: 'PAY_PER_REQUEST',
        PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: false },
      }),
    });
  table('thalweg-stations', [{ AttributeName: 'id', KeyType: 'HASH' }], 'Retain');
  table('thalweg-gaugings', [{ AttributeName: 'station_id', KeyType: 'HASH' }, { AttributeName: 't', KeyType: 'RANGE' }], 'Retain');
  table('thalweg-runs', [{ AttributeName: 'run_id', KeyType: 'HASH' }], 'Delete');
  table('thalweg-warnings', [{ AttributeName: 'id', KeyType: 'HASH' }], 'Delete');
  table('thalweg-budget', [{ AttributeName: 'day', KeyType: 'HASH' }], 'Delete');
  t.data.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'thalweg-runs',
    GlobalSecondaryIndexes: [Match.objectLike({ IndexName: 'by_station', KeySchema: [{ AttributeName: 'station_id', KeyType: 'HASH' }, { AttributeName: 'started', KeyType: 'RANGE' }] })],
    TimeToLiveSpecification: { AttributeName: 'ttl', Enabled: true },
  });
  t.data.hasResourceProperties('AWS::DynamoDB::Table', {
    TableName: 'thalweg-warnings',
    GlobalSecondaryIndexes: [Match.objectLike({ IndexName: 'by_state', KeySchema: [{ AttributeName: 'state', KeyType: 'HASH' }, { AttributeName: 'created', KeyType: 'RANGE' }] })],
  });
});

test('workflow: activity, standard state machine with X-Ray, disabled replay', () => {
  t.flow.hasResourceProperties('AWS::StepFunctions::Activity', { Name: 'thalweg-agent' });
  t.flow.hasResourceProperties('AWS::StepFunctions::StateMachine', {
    StateMachineName: 'thalweg-gauging',
    StateMachineType: 'STANDARD',
    TracingConfiguration: { Enabled: true },
    LoggingConfiguration: Match.objectLike({ Level: 'ERROR' }),
  });
  t.flow.hasResourceProperties('AWS::Scheduler::Schedule', { Name: 'thalweg-replay', State: 'DISABLED', ScheduleExpression: 'rate(15 minutes)' });
  t.flow.hasResourceProperties('AWS::Events::Rule', {
    EventPattern: Match.objectLike({ detail: Match.objectLike({ object: { key: [{ wildcard: 'clips/*.json' }] } }) }),
  });
});

test('every Lambda is Python 3.12 on arm64 with a 14-day log group', () => {
  let count = 0;
  for (const tpl of all) {
    for (const fn of Object.values(tpl.findResources('AWS::Lambda::Function'))) {
      count++;
      assert.deepEqual(fn.Properties.Architectures, ['arm64']);
      assert.equal(fn.Properties.Runtime, 'python3.12');
      assert.equal(fn.Properties.TracingConfig.Mode, 'Active');
    }
    for (const lg of Object.values(tpl.findResources('AWS::Logs::LogGroup'))) {
      assert.equal(lg.Properties.RetentionInDays, 14);
    }
  }
  assert.equal(count, 10);
  t.api.hasResourceProperties('AWS::Lambda::Function', { FunctionName: 'thalweg-api', MemorySize: 512 });
});

test('API routes: bare API for the CDN, site API as the front door', () => {
  const apis = t.api.findResources('AWS::ApiGatewayV2::Api');
  const idOf = (name: string) => Object.entries(apis).find(([, r]) => r.Properties.Name === name)![0];
  const routesOf = (name: string) =>
    Object.values(t.api.findResources('AWS::ApiGatewayV2::Route'))
      .filter((r) => r.Properties.ApiId.Ref === idOf(name))
      .map((r) => r.Properties.RouteKey)
      .sort();
  assert.deepEqual(routesOf('thalweg-site'), ['ANY /api/{proxy+}', 'GET /', 'GET /artifacts/{proxy+}', 'GET /{proxy+}']);
  const routes = routesOf('thalweg');
  assert.deepEqual(routes, [
    'GET /health',
    'GET /reviews',
    'GET /runs',
    'GET /runs/{id}',
    'GET /stations',
    'GET /stations/{id}',
    'GET /stations/{id}/gaugings',
    'POST /reviews/{id}',
    'POST /runs',
  ]);
});

test('CloudFront: web default, /artifacts/* and /api/* behaviours, no error rewrites', () => {
  const dist = Object.values(t.web.findResources('AWS::CloudFront::Distribution'))[0].Properties.DistributionConfig;
  assert.deepEqual(dist.CacheBehaviors.map((b: { PathPattern: string }) => b.PathPattern), ['/artifacts/*', '/api/*']);
  assert.equal(dist.CustomErrorResponses, undefined);
  assert.equal(dist.DefaultCacheBehavior.FunctionAssociations.length, 1);
  t.web.resourceCountIs('AWS::CloudFront::OriginAccessControl', 2);
});

test('ops: dashboard, alarm, $25 budget on the project tag', () => {
  t.ops.hasResourceProperties('AWS::CloudWatch::Dashboard', { DashboardName: 'thalweg' });
  t.ops.hasResourceProperties('AWS::CloudWatch::Alarm', { AlarmName: 'thalweg-executions-failed', MetricName: 'ExecutionsFailed' });
  t.ops.hasResourceProperties('AWS::Budgets::Budget', {
    Budget: Match.objectLike({
      BudgetName: 'thalweg',
      BudgetLimit: { Amount: 25, Unit: 'USD' },
      CostFilters: { TagKeyValue: ['user:project$thalweg'] },
    }),
    NotificationsWithSubscribers: [Match.objectLike({ Notification: Match.objectLike({ Threshold: 80, ThresholdType: 'PERCENTAGE' }) })],
  });
});

test('cost guard rails: no NAT, no VPC endpoints, no KMS keys, no Secrets Manager', () => {
  for (const tpl of all) {
    for (const type of ['AWS::EC2::NatGateway', 'AWS::EC2::VPCEndpoint', 'AWS::KMS::Key', 'AWS::SecretsManager::Secret']) {
      tpl.resourceCountIs(type, 0);
    }
  }
});
