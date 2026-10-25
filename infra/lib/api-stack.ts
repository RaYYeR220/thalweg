import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as apigwv2 from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import { NAMES, pyFunction } from './common';
import { DataStack } from './data-stack';
import { FlowStack } from './flow-stack';

export interface ApiStackProps extends StackProps {
  readonly data: DataStack;
  readonly flow: FlowStack;
}

/**
 * HTTP API with a single router Lambda, served to browsers at /api/* through CloudFront.
 *
 * A second HTTP API, `thalweg-site` (`SiteUrl`), is the product's front door when no CDN is in
 * front: `/` and `/artifacts/*` come from S3 through `thalweg-web` and `/api/*` maps onto the
 * same routes, so the URL layout is identical with or without CloudFront.
 */
export class ApiStack extends Stack {
  readonly api: apigwv2.HttpApi;
  /** The same routes under /api/*, plus the web app and /artifacts/* (front door without the CDN). */
  readonly site: apigwv2.HttpApi;
  readonly handler: lambda.Function;
  readonly webHandler: lambda.Function;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const { data, flow } = props;

    this.handler = pyFunction(this, 'Router', {
      functionName: 'thalweg-api',
      handler: 'api.router.handler',
      description: 'Thalweg API: stations, gaugings, runs, reviews.',
      memorySize: 512,
      timeout: Duration.seconds(20),
      environment: {
        TABLE_STATIONS: data.stations.tableName,
        TABLE_GAUGINGS: data.gaugings.tableName,
        TABLE_RUNS: data.runs.tableName,
        TABLE_WARNINGS: data.warnings.tableName,
        TABLE_BUDGET: data.budget.tableName,
        INGEST_BUCKET: data.ingest.bucketName,
        ARTIFACTS_BUCKET: data.artifacts.bucketName,
        STATE_MACHINE_ARN: flow.stateMachine.stateMachineArn,
        PASSCODE_PARAM: NAMES.params.reviewerPasscode,
        SERVICE_TOKEN_PARAM: NAMES.params.serviceToken,
        JUDGE_LLM_DAILY_CAP: '40',
        JUDGE_MODEL: NAMES.models.judge,
        ALLOWED_MODELS: NAMES.models.allowed.join(','),
        APP_VERSION: this.node.tryGetContext('appVersion') ?? 'dev',
      },
    });
    const fn = this.handler;
    data.stations.grantReadData(fn);
    data.gaugings.grantReadData(fn);
    data.runs.grantReadWriteData(fn); // run reads + in-flight locks
    data.warnings.grantReadWriteData(fn); // review reads + sign_lock
    data.budget.grantReadWriteData(fn); // judge LLM run counter
    data.ingest.grantRead(fn, 'clips/*');
    data.artifacts.grantRead(fn, 'runs/*'); // presigned trace URLs
    flow.stateMachine.grantStartExecution(fn);
    flow.stateMachine.grantTaskResponse(fn);
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['ssm:GetParameter'],
        resources: [NAMES.params.reviewerPasscode, NAMES.params.serviceToken].map((p) =>
          this.formatArn({ service: 'ssm', resource: 'parameter', resourceName: p.replace(/^\//, '') }),
        ),
      }),
    );

    this.api = new apigwv2.HttpApi(this, 'Http', {
      apiName: 'thalweg',
      description: 'Thalweg public API (behind CloudFront at /api).',
      corsPreflight: {
        allowOrigins: ['http://localhost:3000'],
        allowMethods: [apigwv2.CorsHttpMethod.GET, apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.OPTIONS],
        allowHeaders: ['content-type', 'authorization'],
        maxAge: Duration.hours(1),
      },
    });
    const stage = this.api.defaultStage!.node.defaultChild as apigwv2.CfnStage;
    stage.defaultRouteSettings = { throttlingRateLimit: 25, throttlingBurstLimit: 50 };

    const integration = new HttpLambdaIntegration('Router', fn, {
      payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_2_0,
    });
    const routes: Array<[apigwv2.HttpMethod, string]> = [
      [apigwv2.HttpMethod.GET, '/health'],
      [apigwv2.HttpMethod.GET, '/stations'],
      [apigwv2.HttpMethod.GET, '/stations/{id}'],
      [apigwv2.HttpMethod.GET, '/stations/{id}/gaugings'],
      [apigwv2.HttpMethod.GET, '/runs'],
      [apigwv2.HttpMethod.GET, '/runs/{id}'],
      [apigwv2.HttpMethod.POST, '/runs'],
      [apigwv2.HttpMethod.GET, '/reviews'],
      [apigwv2.HttpMethod.POST, '/reviews/{id}'],
    ];
    for (const [method, routePath] of routes) {
      this.api.addRoutes({ path: routePath, methods: [method], integration });
    }

    // --- front door without the CDN ---------------------------------------------
    // A second HTTP API with the CloudFront URL layout: /api/* -> the router, /artifacts/* and
    // everything else -> S3. Kept apart from the bare API so web pages such as /stations/dart/
    // never collide with API routes.
    this.webHandler = pyFunction(this, 'Web', {
      functionName: 'thalweg-web',
      handler: 'api.static.handler',
      description: 'Serves the web app and public artifacts from S3 on the site API.',
      memorySize: 256,
      timeout: Duration.seconds(10),
      environment: {
        WEB_BUCKET: data.web.bucketName,
        ARTIFACTS_BUCKET: data.artifacts.bucketName,
      },
    });
    data.web.grantRead(this.webHandler);
    for (const prefix of ['runs/*', 'stations/*', 'public/*']) {
      data.artifacts.grantRead(this.webHandler, prefix);
    }

    this.site = new apigwv2.HttpApi(this, 'Site', {
      apiName: 'thalweg-site',
      description: 'Thalweg web app, /api and /artifacts on one origin (front door without the CDN).',
    });
    (this.site.defaultStage!.node.defaultChild as apigwv2.CfnStage).defaultRouteSettings = {
      throttlingRateLimit: 50,
      throttlingBurstLimit: 100,
    };
    const siteApi = new HttpLambdaIntegration('SiteRouter', fn, {
      payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_2_0,
    });
    const siteWeb = new HttpLambdaIntegration('SiteWeb', this.webHandler, {
      payloadFormatVersion: apigwv2.PayloadFormatVersion.VERSION_2_0,
    });
    this.site.addRoutes({ path: '/api/{proxy+}', methods: [apigwv2.HttpMethod.ANY], integration: siteApi });
    for (const routePath of ['/', '/{proxy+}', '/artifacts/{proxy+}']) {
      this.site.addRoutes({ path: routePath, methods: [apigwv2.HttpMethod.GET], integration: siteWeb });
    }

    new CfnOutput(this, 'ApiUrl', { value: this.api.apiEndpoint });
    new CfnOutput(this, 'ApiId', { value: this.api.apiId });
    new CfnOutput(this, 'SiteUrl', { value: this.site.apiEndpoint, description: 'Web app + /api + /artifacts without the CDN' });
    new CfnOutput(this, 'SiteApiId', { value: this.site.apiId });
  }
}
