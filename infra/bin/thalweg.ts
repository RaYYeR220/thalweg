#!/usr/bin/env node
import { App, Tags } from 'aws-cdk-lib';
import { ApiStack } from '../lib/api-stack';
import { DataStack } from '../lib/data-stack';
import { FlowStack } from '../lib/flow-stack';
import { OpsStack } from '../lib/ops-stack';
import { WebStack } from '../lib/web-stack';
import { WorkerStack } from '../lib/worker-stack';

const app = new App();
const ctx = (key: string): string | undefined => {
  const v = app.node.tryGetContext(key);
  return v === undefined || v === null ? undefined : String(v);
};

const env = {
  account: ctx('account') ?? process.env.CDK_DEFAULT_ACCOUNT,
  region: ctx('region') ?? process.env.CDK_DEFAULT_REGION ?? 'us-east-1',
};
Tags.of(app).add('project', 'thalweg');

const data = new DataStack(app, 'ThalwegData', {
  env,
  description: 'Thalweg: clip ingest, artifacts, stations, gaugings, runs, warnings, budget',
});
const flow = new FlowStack(app, 'ThalwegFlow', {
  env,
  data,
  replayEnabled: ctx('replay') === 'enabled',
  description: 'Thalweg: gauging workflow (Step Functions + agent activity), ingest trigger, replay',
});
const api = new ApiStack(app, 'ThalwegApi', { env, data, flow, description: 'Thalweg: HTTP API' });
new WebStack(app, 'ThalwegWeb', { env, data, api, description: 'Thalweg: web app and CDN' });
new OpsStack(app, 'ThalwegOps', {
  env,
  flow,
  api,
  monthlyBudgetUsd: Number(ctx('monthlyBudgetUsd') ?? 25),
  description: 'Thalweg: dashboard, alarms, budget',
});

// The Graviton worker is opt-in: `-c worker=true -c coolAmi=ami-...`.
if (ctx('worker') === 'true') {
  const coolAmi = ctx('coolAmi');
  if (!coolAmi || !/^ami-[0-9a-f]{8,17}$/.test(coolAmi)) {
    throw new Error('ThalwegWorker needs -c coolAmi=<ami-id> (the subscribed COOL Graviton4 AMI)');
  }
  new WorkerStack(app, 'ThalwegWorker', {
    env,
    data,
    flow,
    coolAmi,
    instanceType: ctx('workerInstanceType') ?? 'c8g.large',
    rootDevice: ctx('rootDevice') ?? '/dev/sda1',
    marketplaceMetering: ctx('marketplaceMetering') === 'true',
    description: 'Thalweg: Graviton4 worker on the COOL AMI',
  });
}
