# Building replay features on AWS Lambda

Goal: keep the replay archive in S3 and rebuild every derived table (frame features, touch
events, decision rows) for the whole archive in about ten minutes, for a few dollars, by
running one Lambda invocation per replay. Training stays local (or on one large instance).

Written for a first AWS account. Region used throughout: `us-east-1` (cheapest, most quota).

## Cost estimate

Assumes 140,000 replays (the Kaggle set) at 0.85 MB each, Lambda at 1 GB memory, and the
current extractor speeds (about 0.3 s for frame features plus touches, about 5 s for the
decision rows). Prices are on-demand list prices for us-east-1 in late 2026; check the
calculator before relying on them.

| item | how it's billed | one full rebuild | per month, idle |
| --- | --- | --- | --- |
| S3 raw archive, 120 GB | $0.023 per GB-month | — | $2.80 |
| S3 derived parquet, about 100 GB (decisions at 5 Hz dominate) | $0.023 per GB-month | — | $2.30 |
| S3 requests (1 GET + 3 PUT per replay) | $0.0004 per 1k GET, $0.005 per 1k PUT | $2.20 | — |
| Lambda compute, 140k × 5.3 s × 1 GB | $0.0000167 per GB-s | $12.40 | — |
| Lambda requests | $0.20 per million | $0.03 | — |
| Step Functions distributed map (child executions) | about $0.00001 per step + duration | $2 to $4 | — |
| ECR image storage, 1 GB | $0.10 per GB-month | — | $0.10 |
| Data transfer out, syncing derived parquet to your PC | $0.09 per GB after 100 GB free per month | $0 to $9 | — |
| CloudWatch logs (keep retention at 7 days) | $0.50 per GB ingested | about $1 | — |

Totals: **roughly $20 for a full 140k-replay rebuild, about $5 a month to keep the archive.**
Touches and frame features alone (no decision rows) are under $3 per rebuild. Lambda's
always-free tier (400,000 GB-seconds and 1M requests a month) covers incremental builds of a
few thousand replays at no cost. The one line to watch is transfer out: sync only the
tables you need locally (touches and frame features are a few GB), and do large fits on an
EC2 instance in the same region instead of pulling 100 GB home.

Guardrail: create an AWS Budget with an alert at $25/month on day one. Nothing below can run
away faster than that alert, except a bug that loops invocations, which the distributed
map's failure threshold stops.

## 1. Account and tools (one hour)

1. Create the account at aws.amazon.com. Turn on MFA for the root user, then stop using root.
2. IAM Identity Center → create a user for yourself with the `AdministratorAccess` permission
   set. This is the normal way to log in and to get CLI credentials.
3. Billing → Budgets → create a monthly cost budget, $25, email alert at 80% and 100%.
4. Install the AWS CLI v2 and Docker Desktop on the PC. Then:

```powershell
aws configure sso          # paste the Identity Center start URL, pick the account, name the profile "rl"
$env:AWS_PROFILE = "rl"
aws sts get-caller-identity
```

## 2. Bucket and layout (ten minutes)

```powershell
$BUCKET = "rl-replays-<something-unique>"
aws s3 mb s3://$BUCKET --region us-east-1
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Suspended
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

Layout:

```
raw/2v2/<replay_id>.replay          immutable archive (Kaggle subset now, crawler output later)
meta/2v2/metadata.json              line-delimited, same format as today
features/v1/frames/<id>.parquet     one file per replay, written by Lambda
features/v1/touches/<id>.parquet
features/v1/decisions/<id>.parquet
manifests/2v2-all.csv               list of raw keys for the map to iterate
```

Upload what's already on D: (this is the only slow step; 3.2 GB takes minutes, 120 GB takes
hours on a home connection, so do the Kaggle download inside AWS instead when you go bigger):

```powershell
aws s3 sync D:\RocketLeague-Analytics\data\raw\2v2 s3://$BUCKET/raw/2v2 --size-only
aws s3 cp D:\RocketLeague-Analytics\data\meta\2v2.metadata.json s3://$BUCKET/meta/2v2/metadata.json
```

Bump `v1` whenever an extractor changes; old versions stay for comparison and the Lambda
skips any output that already exists for its version.

## 3. The Lambda (half a day, mostly testing)

Add `cloud/` to the repo:

`cloud/Dockerfile`

```dockerfile
FROM public.ecr.aws/lambda/python:3.12
RUN pip install --no-cache-dir "polars>=1.0" "pyarrow>=15" "numpy>=2,<3" boto3
# rrrocket: static Linux build from nickbabcock/rrrocket releases (pin the version)
ADD https://github.com/nickbabcock/rrrocket/releases/download/v0.11.8/rrrocket-0.11.8-x86_64-unknown-linux-musl.tar.gz /tmp/rr.tgz
RUN tar -xzf /tmp/rr.tgz -C /opt && mv /opt/rrrocket*/rrrocket /opt/rrrocket && chmod +x /opt/rrrocket
ENV RRROCKET=/opt/rrrocket
COPY rlreplay ${LAMBDA_TASK_ROOT}/rlreplay
COPY cloud/handler.py ${LAMBDA_TASK_ROOT}/handler.py
CMD ["handler.main"]
```

`cloud/handler.py`

```python
import json, os, tempfile
from pathlib import Path
import boto3, polars as pl
from rlreplay.header import parse
from rlreplay.frames import extract
from rlreplay.events import extract_touches
from rlreplay.decisions import build_rows

s3 = boto3.client("s3")
VERSION = os.environ.get("FEATURE_VERSION", "v1")
TABLES = {"frames": lambda r, rid: extract(r, replay_id=rid),
          "touches": lambda r, rid: extract_touches(r, replay_id=rid),
          "decisions": lambda r, rid: build_rows(r, replay_id=rid, hz=5.0, horizon=2.5)}

def exists(bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key); return True
    except s3.exceptions.ClientError:
        return False

def main(event, context):
    bucket, key = event["bucket"], event["key"]           # raw/2v2/<id>.replay
    replay_id = Path(key).stem
    wanted = [t for t in event.get("tables", list(TABLES)) if not exists(bucket, f"features/{VERSION}/{t}/{replay_id}.parquet")]
    if not wanted:
        return {"replay_id": replay_id, "skipped": True}
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"{replay_id}.replay"
        s3.download_file(bucket, key, str(local))
        parsed = parse(local, network=True)
        for table in wanted:
            rows = TABLES[table](parsed, replay_id)
            if not rows:
                continue
            out = Path(tmp) / f"{table}.parquet"
            pl.DataFrame(rows, infer_schema_length=None).write_parquet(out)
            s3.upload_file(str(out), bucket, f"features/{VERSION}/{table}/{replay_id}.parquet")
    return {"replay_id": replay_id, "built": wanted}
```

Note: the handler builds the decision rows at the 2.5 s horizon and the extractor code is
whatever is in the image, so the image tag is the provenance of every parquet file.

Build, test locally, push:

```powershell
docker build -f cloud/Dockerfile -t rl-features .
# local smoke test with the Lambda runtime emulator (needs AWS creds in env for S3)
docker run --rm -p 9000:8080 -e AWS_PROFILE=rl -v $HOME\.aws:/root/.aws:ro rl-features
# in another shell:
curl -XPOST "http://localhost:9000/2015-03-31/functions/function/invocations" -d '{"bucket":"<bucket>","key":"raw/2v2/<some id>.replay"}'

$ACCOUNT = aws sts get-caller-identity --query Account --output text
aws ecr create-repository --repository-name rl-features
aws ecr get-login-password | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com"
docker tag rl-features "$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/rl-features:v1"
docker push "$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/rl-features:v1"
```

Role and function:

```powershell
# execution role: Lambda basics + read/write on the one bucket
aws iam create-role --role-name rl-features-lambda --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name rl-features-lambda --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam put-role-policy --role-name rl-features-lambda --policy-name bucket --policy-document "{`"Version`":`"2012-10-17`",`"Statement`":[{`"Effect`":`"Allow`",`"Action`":[`"s3:GetObject`",`"s3:PutObject`",`"s3:HeadObject`",`"s3:ListBucket`"],`"Resource`":[`"arn:aws:s3:::$BUCKET`",`"arn:aws:s3:::$BUCKET/*`"]}]}"

aws lambda create-function --function-name rl-features --package-type Image `
  --code ImageUri="$ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/rl-features:v1" `
  --role "arn:aws:iam::${ACCOUNT}:role/rl-features-lambda" --memory-size 1024 --timeout 300 `
  --environment "Variables={FEATURE_VERSION=v1}" --ephemeral-storage Size=1024
aws lambda invoke --function-name rl-features --cli-binary-format raw-in-base64-out `
  --payload '{"bucket":"<bucket>","key":"raw/2v2/<some id>.replay"}' out.json; cat out.json
```

Memory: 1024 MB gives Lambda a bit over half a vCPU; the parsers are single-threaded so
more memory mostly buys CPU speed. Try 1769 MB (one full vCPU) and compare cost per replay;
it is often cheaper because duration drops more than price rises.

## 4. Fan-out with Step Functions distributed map (one hour)

A distributed map reads a manifest or lists an S3 prefix and runs the Lambda for each item,
up to 10,000 concurrently, with retries and a tolerated-failure percentage, and writes a
results summary back to S3.

Create a role for Step Functions that can invoke the function and read/write the bucket
(same pattern as above with `states.amazonaws.com` as principal), then the state machine:

```json
{
  "StartAt": "BuildAll",
  "States": {
    "BuildAll": {
      "Type": "Map",
      "ItemReader": {
        "Resource": "arn:aws:states:::s3:listObjectsV2",
        "Parameters": {"Bucket": "<bucket>", "Prefix": "raw/2v2/"}
      },
      "ItemSelector": {"bucket": "<bucket>", "key.$": "$$.Map.Item.Value.Key"},
      "ItemProcessor": {
        "ProcessorConfig": {"Mode": "DISTRIBUTED", "ExecutionType": "EXPRESS"},
        "StartAt": "Build",
        "States": {
          "Build": {
            "Type": "Task",
            "Resource": "arn:aws:states:::lambda:invoke",
            "Parameters": {"FunctionName": "rl-features", "Payload.$": "$"},
            "Retry": [{"ErrorEquals": ["States.ALL"], "IntervalSeconds": 5, "MaxAttempts": 3, "BackoffRate": 2}],
            "End": true
          }
        }
      },
      "MaxConcurrency": 1000,
      "ToleratedFailurePercentage": 1,
      "ResultWriter": {
        "Resource": "arn:aws:states:::s3:putObject",
        "Parameters": {"Bucket": "<bucket>", "Prefix": "manifests/results/"}
      },
      "End": true
    }
  }
}
```

```powershell
aws stepfunctions create-state-machine --name rl-build-all --type STANDARD --definition file://cloud/build_all.json --role-arn arn:aws:iam::${ACCOUNT}:role/rl-features-sfn
aws stepfunctions start-execution --state-machine-arn arn:aws:states:us-east-1:${ACCOUNT}:stateMachine:rl-build-all
```

Concurrency: a new account's Lambda quota is 1,000 concurrent executions (sometimes lower
for brand-new accounts; check Service Quotas → Lambda → Concurrent executions). At 1,000
the 3k-replay set builds in under a minute and 140k in about 12 minutes. Ask for 3,000 via
Service Quotas when you need it; it's usually approved within a day.

## 5. Keep it current without batch runs

S3 event notification on `raw/2v2/` object-created → the same Lambda (direct trigger, no map).
The crawler on the homelab then uploads each new replay as it lands and features appear
seconds later. Set up with `aws s3api put-bucket-notification-configuration` after adding a
resource-based permission for S3 to invoke the function.

## 6. Pulling results home and fitting

```powershell
aws s3 sync s3://$BUCKET/features/v1/touches  D:\RocketLeague-Analytics\data\features\cloud\touches
aws s3 sync s3://$BUCKET/features/v1/frames   D:\RocketLeague-Analytics\data\features\cloud\frames
```

Touches and frame features for 140k replays are a few GB; the existing `xg`, `value` and
`validate` commands accept a directory of per-replay parquet files via a glob with a
one-line change (`pl.scan_parquet("dir/*.parquet")`). Decision rows at 5 Hz are about 100
GB for the full set; fit those on an EC2 `r7i.4xlarge` or similar in us-east-1 (spot, about
$0.35 an hour) with the bucket mounted or synced to its disk, and bring back only the model
and reports.

## What can go wrong

- **rrrocket version drift.** Pin the release in the Dockerfile and record the image tag in
  the parquet path (`v1`). Validate a 100-replay sample from the cloud against a local build
  before trusting a version.
- **A reclaimed or timed-out invocation.** Outputs are written whole or not at all, and the
  handler skips existing outputs, so a retry is safe.
- **Runaway cost.** Budget alert plus `ToleratedFailurePercentage` plus a sane `MaxConcurrency`.
  A Lambda cannot loop on itself here; the map decides how many run.
- **Kaggle inside AWS.** If you later download the full Kaggle set from Lambda or EC2, Kaggle's
  per-IP throttle applies per NAT address; spreading over several instances or a small
  Fargate task pool works, hammering from one box does not.
