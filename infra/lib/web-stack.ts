import * as path from 'path';
import { Annotations, CfnOutput, Duration, Fn, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { ApiStack } from './api-stack';
import { FUNCTIONS_DIR } from './common';
import { DataStack } from './data-stack';

export interface WebStackProps extends StackProps {
  readonly data: DataStack;
  readonly api: ApiStack;
}

/**
 * One origin for the product: the static web app, run evidence under /artifacts/*, and the
 * API under /api/*. Same origin, so the browser needs no CORS.
 */
export class WebStack extends Stack {
  readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);
    const { data, api } = props;

    const viewerFn = (id: string, file: string, comment: string) =>
      new cloudfront.Function(this, id, {
        functionName: `thalweg-${file.replace(/\.js$/, '')}`,
        comment,
        runtime: cloudfront.FunctionRuntime.JS_2_0,
        code: cloudfront.FunctionCode.fromFile({ filePath: path.join(FUNCTIONS_DIR, file) }),
      });
    const webIndex = viewerFn('WebIndex', 'web-index.js', 'Static export: append index.html to directory paths');
    const artifactsPath = viewerFn('ArtifactsPath', 'artifacts-path.js', 'Strip /artifacts; serve only runs/, stations/, public/');
    const apiPath = viewerFn('ApiPath', 'api-path.js', 'Strip /api for the HTTP API origin');

    // Both buckets belong to ThalwegData, whose policies grant this account's distributions
    // read access (web: everything; artifacts: runs/, stations/, public/).
    const imported = (id: string, bucket: s3.IBucket) =>
      s3.Bucket.fromBucketAttributes(this, id, {
        bucketName: bucket.bucketName,
        bucketRegionalDomainName: bucket.bucketRegionalDomainName,
      });
    const webOrigin = origins.S3BucketOrigin.withOriginAccessControl(imported('WebRef', data.web), {
      originAccessLevels: [cloudfront.AccessLevel.READ],
    });
    const artifactsOrigin = origins.S3BucketOrigin.withOriginAccessControl(imported('ArtifactsRef', data.artifacts), {
      originAccessLevels: [cloudfront.AccessLevel.READ],
    });

    const artifactsCache = new cloudfront.CachePolicy(this, 'ArtifactsCache', {
      cachePolicyName: 'thalweg-artifacts',
      comment: 'Evidence and results: 1 day unless the object sets Cache-Control',
      defaultTtl: Duration.days(1),
      minTtl: Duration.seconds(0),
      maxTtl: Duration.days(365),
      enableAcceptEncodingGzip: true,
      enableAcceptEncodingBrotli: true,
      queryStringBehavior: cloudfront.CacheQueryStringBehavior.none(),
      headerBehavior: cloudfront.CacheHeaderBehavior.none(),
      cookieBehavior: cloudfront.CacheCookieBehavior.none(),
    });

    const apiDomain = Fn.select(2, Fn.split('/', api.api.apiEndpoint));
    const apiOrigin = new origins.HttpOrigin(apiDomain, {
      protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
      originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
    });

    this.distribution = new cloudfront.Distribution(this, 'Cdn', {
      comment: 'thalweg',
      defaultRootObject: 'index.html',
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      defaultBehavior: {
        origin: webOrigin,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
        responseHeadersPolicy: cloudfront.ResponseHeadersPolicy.SECURITY_HEADERS,
        compress: true,
        functionAssociations: [{ function: webIndex, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors: {
        '/artifacts/*': {
          origin: artifactsOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          cachePolicy: artifactsCache,
          compress: true,
          functionAssociations: [{ function: artifactsPath, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
        },
        '/api/*': {
          origin: apiOrigin,
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          functionAssociations: [{ function: apiPath, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
        },
      },
      // No custom error responses: they would also rewrite the API's 403/404/409 bodies.
    });

    Annotations.of(this).acknowledgeWarning(
      '@aws-cdk/aws-cloudfront-origins:updateImportedBucketPolicyOac',
      'The ThalwegData bucket policies grant this account\'s distributions read through OAC.',
    );

    new CfnOutput(this, 'DistributionDomain', { value: this.distribution.distributionDomainName });
    new CfnOutput(this, 'DistributionId', { value: this.distribution.distributionId });
    new CfnOutput(this, 'SiteUrl', { value: `https://${this.distribution.distributionDomainName}` });
  }
}
