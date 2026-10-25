import { CfnOutput, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import { LOG_RETENTION, NAMES } from './common';
import { DataStack } from './data-stack';
import { FlowStack } from './flow-stack';

export interface WorkerStackProps extends StackProps {
  readonly data: DataStack;
  readonly flow: FlowStack;
  /** The COOL (OpenCV on Graviton) Marketplace AMI for this region. */
  readonly coolAmi: string;
  readonly instanceType?: string;
  /** Root device of the AMI (`/dev/sda1` for the COOL images). */
  readonly rootDevice?: string;
  /** Grant aws-marketplace:RegisterUsage/MeterUsage, for AMIs that meter from the instance. */
  readonly marketplaceMetering?: boolean;
  /** Root volume size; must be at least the AMI snapshot size (60 GB for COOL v3). */
  readonly rootGb?: number;
}

/**
 * The Graviton4 worker: one instance from the COOL AMI in a public subnet of the default VPC.
 * No inbound rules and no SSH; it is reached through SSM Session Manager only. It polls the
 * `thalweg-agent` activity, runs the agent with COOL's OpenCV 5 build and writes artifacts.
 */
export class WorkerStack extends Stack {
  constructor(scope: Construct, id: string, props: WorkerStackProps) {
    super(scope, id, props);
    const { data, flow } = props;

    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVpc', { isDefault: true });
    const sg = new ec2.SecurityGroup(this, 'WorkerSg', {
      vpc,
      securityGroupName: 'thalweg-worker',
      description: 'Thalweg worker: egress only, no inbound',
      allowAllOutbound: true,
    });

    const logGroup = new logs.LogGroup(this, 'WorkerLogs', {
      logGroupName: '/thalweg/worker',
      retention: LOG_RETENTION,
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const role = new iam.Role(this, 'WorkerRole', {
      roleName: 'thalweg-worker',
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      description: 'Thalweg Graviton worker (SSM-managed, least privilege)',
      managedPolicies: [iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore')],
    });
    // Step Functions activity: poll, heartbeat, report.
    flow.activity.grant(role, 'states:GetActivityTask');
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['states:SendTaskSuccess', 'states:SendTaskFailure', 'states:SendTaskHeartbeat'],
        resources: [flow.activity.activityArn],
      }),
    );
    // Clips in, artifacts out.
    data.ingest.grantRead(role);
    data.artifacts.grantReadWrite(role);
    // Every thalweg-* table (runs, gaugings, stations, warnings, the LLM budget counter).
    for (const t of [data.stations, data.gaugings, data.runs, data.warnings, data.budget]) {
      t.grantReadWriteData(role);
    }
    // /thalweg/* parameters (Venice key, MCP token). SecureStrings use the AWS-managed SSM key.
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter', 'ssm:GetParameters'],
        resources: [this.formatArn({ service: 'ssm', resource: 'parameter', resourceName: 'thalweg/*' })],
      }),
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['cloudwatch:PutMetricData'],
        resources: ['*'],
        conditions: { StringEquals: { 'cloudwatch:namespace': NAMES.metricsNamespace } },
      }),
    );
    logGroup.grantWrite(role);
    if (props.marketplaceMetering) {
      role.addToPolicy(
        new iam.PolicyStatement({
          actions: ['aws-marketplace:RegisterUsage', 'aws-marketplace:MeterUsage'],
          resources: ['*'],
        }),
      );
    }

    const envFile = [
      `AWS_REGION=${this.region}`,
      `AWS_DEFAULT_REGION=${this.region}`,
      `THALWEG_ACTIVITY_ARN=${flow.activity.activityArn}`,
      `THALWEG_STATE_MACHINE_ARN=${flow.stateMachine.stateMachineArn}`,
      `THALWEG_INGEST_BUCKET=${data.ingest.bucketName}`,
      `THALWEG_ARTIFACTS_BUCKET=${data.artifacts.bucketName}`,
      `THALWEG_TABLE_STATIONS=${data.stations.tableName}`,
      `THALWEG_TABLE_GAUGINGS=${data.gaugings.tableName}`,
      `THALWEG_TABLE_RUNS=${data.runs.tableName}`,
      `THALWEG_TABLE_WARNINGS=${data.warnings.tableName}`,
      `THALWEG_TABLE_BUDGET=${data.budget.tableName}`,
      `THALWEG_PARAM_VENICE_KEY=${NAMES.params.veniceKey}`,
      `THALWEG_PARAM_MCP_TOKEN=${NAMES.params.serviceToken}`,
      `THALWEG_LOG_GROUP=${logGroup.logGroupName}`,
      `THALWEG_METRICS_NAMESPACE=${NAMES.metricsNamespace}`,
      'VENICE_BASE_URL=https://api.venice.ai/api/v1',
    ].join('\n');

    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      'set -euo pipefail',
      "cat > /etc/thalweg.env <<'THALWEG_ENV'",
      envFile,
      'THALWEG_ENV',
      'chmod 0644 /etc/thalweg.env',
    );

    const instance = new ec2.Instance(this, 'Worker', {
      instanceName: 'thalweg-worker',
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      associatePublicIpAddress: true,
      securityGroup: sg,
      instanceType: new ec2.InstanceType(props.instanceType ?? 'c8g.large'),
      machineImage: ec2.MachineImage.genericLinux({ [this.region]: props.coolAmi }),
      role,
      userData,
      requireImdsv2: true,
      blockDevices: [
        {
          deviceName: props.rootDevice ?? '/dev/sda1',
          volume: ec2.BlockDeviceVolume.ebs(props.rootGb ?? 64, { volumeType: ec2.EbsDeviceVolumeType.GP3, encrypted: true, deleteOnTermination: true }),
        },
      ],
      ssmSessionPermissions: false,
    });

    new CfnOutput(this, 'InstanceId', { value: instance.instanceId });
    new CfnOutput(this, 'WorkerRoleArn', { value: role.roleArn });
    new CfnOutput(this, 'SessionCommand', { value: `aws ssm start-session --target ${instance.instanceId}` });
  }
}
