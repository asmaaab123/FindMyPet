"""
  /prod/signedurl  – presigned S3 form
  /prod/reports    – store the report + DetectLabels + simple match
"""

import os, json, uuid, datetime, time, logging, boto3
from botocore.exceptions import ClientError, EndpointConnectionError

# ─────── static config ────────────────────────────────────────────────────
BUCKET        = os.environ["UPLOAD_BUCKET"]
TABLE_NAME    = os.environ["TABLE_NAME"]
FRONTEND      = "https://d151tnzh8ma0n4.cloudfront.net"
REKOG_REGION  = os.getenv("REKOG_REGION", "eu-west-1")     # <- west 1
MAX_BYTES     = 4_000_000                                  # 4 MB safety

CORS = {
    "Access-Control-Allow-Origin"     : FRONTEND,
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods"    : "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers"    : (
        "Content-Type,X-Amz-Date,Authorization,"
        "X-Api-Key,X-Amz-Security-Token"
    ),
}

# ─────── AWS clients ──────────────────────────────────────────────────────
s3   = boto3.client("s3")
ddb  = boto3.resource("dynamodb").Table(TABLE_NAME)
rek  = boto3.client("rekognition", region_name=REKOG_REGION)

# ─────── utilities ────────────────────────────────────────────────────────
log = logging.getLogger()
log.setLevel(logging.INFO)

def _resp(code:int, body="", extra=None):
    if not isinstance(body, str):
        body = json.dumps(body)
    return {"statusCode": code,
            "headers"   : {**CORS, **(extra or {})},
            "body"      : body}

def _detect_labels_from_bytes(key:str, *, tries:int=3) -> list[str]:
    """
    Download the object into memory and call Rekognition with Bytes.
    Retries once or twice because S3 may take milliseconds to make
    the new object available for GET after the upload finished.
    """
    for attempt in range(1, tries+1):
        try:
            obj = s3.get_object(Bucket=BUCKET, Key=key)
            bytes_ = obj["Body"].read(MAX_BYTES)
            break
        except ClientError as e:
            if attempt == tries: raise
            time.sleep(0.4)                          # 400 ms back-off
    res = rek.detect_labels(
        Image={"Bytes": bytes_},
        MaxLabels=10,
        MinConfidence=80)
    return [lbl["Name"] for lbl in res["Labels"]]

def _match_found_to_lost(found:set[str]) -> list[dict]:
    resp = ddb.scan(
        FilterExpression="#rt = :lost AND #st = :open",
        ExpressionAttributeNames={"#rt": "reportType", "#st": "status"},
        ExpressionAttributeValues={":lost": "lost", ":open": "OPEN"},
        Limit=50)
    return [
        {"reportId": itm["reportId"],            #  PK
         "type_timestamp": itm["type_timestamp"]}  #  SK
        for itm in resp["Items"]
        if found & set(itm.get("labels", []))
    ]

# ─────── handler ──────────────────────────────────────────────────────────
def lambda_handler(event, _ctx):
    path   = event.get("rawPath") or event.get("path","")
    method = (event.get("requestContext",{}).get("http",{}) or
              {}).get("method") or event.get("httpMethod","")
    method = method.upper()
    log.info("► %s %s", method, path)

    try:
        # CORS pre-flight
        if method == "OPTIONS":
            return _resp(200)

        # POST /signedurl  ───────────────
        if path.endswith("/signedurl") and method == "POST":
            body   = json.loads(event.get("body") or "{}")
            fname  = body.get("filename") or f"{uuid.uuid4()}.jpg"
            rtype  = body.get("reportType","lost")
            key    = f"{rtype}/{fname}"

            form = s3.generate_presigned_post(
                Bucket=BUCKET, Key=key,
                Fields={"success_action_status":"201"},
                Conditions=[
                    {"bucket": BUCKET}, {"key": key},
                    {"success_action_status":"201"},
                    ["content-length-range", 0, 10_000_000]],
                ExpiresIn=900)
            return _resp(200, form)

        # GET /reports  ───────────────────
        if path.endswith("/reports") and method == "GET":
            return _resp(200, ddb.scan(Limit=50)["Items"])

        # POST /reports  ──────────────────
        if path.endswith("/reports") and method == "POST":
            body      = json.loads(event.get("body") or "{}")
            rtype     = body["reportType"]               # lost|found
            key       = body["s3Key"]
            rid       = str(uuid.uuid4())
            ts        = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

            labels = _detect_labels_from_bytes(key)

            item = {
                "reportId"      : rid,
                "type_timestamp": f"{rtype}#{ts}",
                "createdAt"     : ts,
                "reportType"    : rtype,
                "status"        : "OPEN",
                "s3Key"         : key,
                "filename"      : body.get("filename"),
                "labels"        : labels
            }
            ddb.put_item(Item=item)

            # Attempt simple match if FOUND
            if rtype == "found":
                hits = _match_found_to_lost(set(labels))
                if hits:
                    # close every LOST report that overlapped
                    for key in hits:
                        ddb.update_item(
                            Key=key,                             # PK + SK ✔
                            UpdateExpression="SET #s = :m",
                            ExpressionAttributeNames={"#s": "status"},
                            ExpressionAttributeValues={":m": "MATCHED"})
                    # close the current FOUND report too
                    ddb.update_item(
                        Key={"reportId": rid, "type_timestamp": f"{rtype}#{ts}"},
                        UpdateExpression="SET #s = :m",
                        ExpressionAttributeNames={"#s": "status"},
                        ExpressionAttributeValues={":m": "MATCHED"})
            return _resp(201, {"id": rid})

        return _resp(404, {"error":"not found"})

    # ─── error handling ─────────────────
    except EndpointConnectionError as e:
        log.warning("Rekognition endpoint error %s", e)
        return _resp(502, {"awsError": "Rekognition endpoint unreachable"})
    except ClientError as ce:
        log.warning("AWS error: %s", ce)
        return _resp(502, {"awsError": str(ce)})
    except Exception as ex:
        log.exception("🔥 unhandled")
        return _resp(500, {"error": str(ex)})
