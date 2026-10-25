import { CfnOutput, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { NAMES } from './common';

/**
 * Buckets and tables. Everything else references these by name.
 *
 * - ingest: station clips, `clips/<station_id>/<YYYYMMDDTHHMMSS>.mp4` + `.json` sidecar.
 *   EventBridge notifications are on; a sidecar arriving starts a run.
 * - artifacts: `runs/<run_id>/...` (evidence, trace.json), `stations/<id>/...`, `data/...`
 *   (archives, replay manifest; never served), `public/...` (benchmark JSON, warnings.json).
 * - web: the static export of the web app.
 *
 * CloudFront (ThalwegWeb) reads web and the public artifact prefixes through OAC. The bucket
 * policies here grant that to this account's distributions, which avoids a cross-stack cycle.
 */
export class DataStack extends Stack {
  readonly ingest: s3.Bucket;
  readonly artifacts: s3.Bucket;
  readonly web: s3.Bucket;
  readonly stations: dynamodb.Table;
  readonly gaugings: dynamodb.Table;
  readonly runs: dynamodb.Table;
  readonly warnings: dynamodb.Table;
  readonly budget: dynamodb.Table;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    const bucketDefaults: s3.BucketProps = {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
      versioned: false,
    };

    this.ingest = new s3.Bucket(this, 'Ingest', {
      ...bucketDefaults,
      bucketName: `thalweg-ingest-${this.account}`,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    // Native EventBridge delivery (no notification custom resource).
    (this.ingest.node.defaultChild as s3.CfnBucket).notificationConfiguration = {
      eventBridgeConfiguration: { eventBridgeEnabled: true },
    };

    this.artifacts = new s3.Bucket(this, 'Artifacts', {
      ...bucketDefaults,
      bucketName: `thalweg-artifacts-${this.account}`,
      // Holds the extracted station archives and every run's evidence.
      removalPolicy: RemovalPolicy.RETAIN,
      cors: [
        {
          // The web app in local development reads traces through presigned URLs.
          allowedOrigins: ['http://localhost:3000'],
          allowedMethods: [s3.HttpMethods.GET, s3.HttpMethods.HEAD],
          allowedHeaders: ['*'],
          maxAge: 3000,
        },
      ],
    });
    this.artifacts.addToResourcePolicy(this.cloudFrontRead(this.artifacts, ['runs/*', 'stations/*', 'public/*'], 'CloudFrontReadsPublicPrefixes'));

    this.web = new s3.Bucket(this, 'Web', {
      ...bucketDefaults,
      bucketName: `thalweg-web-${this.account}`,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.web.addToResourcePolicy(this.cloudFrontRead(this.web, ['*'], 'CloudFrontReadsSite'));

    const tableDefaults = {
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: false },
    };

    this.stations = new dynamodb.Table(this, 'Stations', {
      ...tableDefaults,
      tableName: NAMES.tables.stations,
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.RETAIN,
    });

    this.gaugings = new dynamodb.Table(this, 'Gaugings', {
      ...tableDefaults,
      tableName: NAMES.tables.gaugings,
      partitionKey: { name: 'station_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 't', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.RETAIN,
    });

    this.runs = new dynamodb.Table(this, 'Runs', {
      ...tableDefaults,
      tableName: NAMES.tables.runs,
      partitionKey: { name: 'run_id', type: dynamodb.AttributeType.STRING },
      // In-flight locks (`lock#<sha1(clip_key|brain)>`) expire on their own.
      timeToLiveAttribute: 'ttl',
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.runs.addGlobalSecondaryIndex({
      indexName: 'by_station',
      partitionKey: { name: 'station_id', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'started', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.warnings = new dynamodb.Table(this, 'Warnings', {
      ...tableDefaults,
      tableName: NAMES.tables.warnings,
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.warnings.addGlobalSecondaryIndex({
      indexName: 'by_state',
      partitionKey: { name: 'state', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'created', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.budget = new dynamodb.Table(this, 'Budget', {
      ...tableDefaults,
      tableName: NAMES.tables.budget,
      partitionKey: { name: 'day', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'ttl',
      removalPolicy: RemovalPolicy.DESTROY,
    });

    const out = (name: string, value: string) => new CfnOutput(this, name, { value });
    out('IngestBucket', this.ingest.bucketName);
    out('ArtifactsBucket', this.artifacts.bucketName);
    out('WebBucket', this.web.bucketName);
    out('StationsTable', this.stations.tableName);
    out('GaugingsTable', this.gaugings.tableName);
    out('RunsTable', this.runs.tableName);
    out('WarningsTable', this.warnings.tableName);
    out('BudgetTable', this.budget.tableName);
  }

  private cloudFrontRead(bucket: s3.Bucket, prefixes: string[], sid: string): iam.PolicyStatement {
    return new iam.PolicyStatement({
      sid,
      principals: [new iam.ServicePrincipal('cloudfront.amazonaws.com')],
      actions: ['s3:GetObject'],
      resources: prefixes.map((p) => bucket.arnForObjects(p)),
      conditions: {
        StringEquals: { 'AWS:SourceAccount': this.account },
        ArnLike: { 'AWS:SourceArn': `arn:${this.partition}:cloudfront::${this.account}:distribution/*` },
      },
    });
  }
}
