# 🐾 Find‑My‑Pet

A tiny **serverless** web app that helps people post *lost* or *found* pet
photos and automatically looks for matches.

---

## How it works (high‑level)

1. **Sign‑in**  
   Browser redirects to **Amazon Cognito**; after login the user gets a secure
   token.

2. **Upload photo**  
   The SPA asks the back‑end for a *presigned* form and pushes the file straight
   to **Amazon S3**.

3. **Save report**  
   A single **AWS Lambda** function (`ApiFunction`) stores the report in
   **DynamoDB** and runs **Rekognition** to detect labels.

4. **Match**  
   If a *found* photo shares a label with a recent *lost* photo, both reports are
   marked **MATCHED**.  
   The browser polls every few seconds and pops a green toast when a match
   appears.

```
Browser SPA
   │
   ├──▶ Cognito  (login / tokens)
   │
   └──▶ API Gateway ─▶ Lambda (ApiFunction)
                      ├── DynamoDB  (PetReports)
                      ├── S3  (findmypet‑uploads)
                      └── Rekognition  (detect labels)
```

---

## Folder structure

```
frontend/          static single‑page‑app (index.html)
lambda/
   └─ lambda_function.py   # ApiFunction
```

---

## Deploy in minutes (manual)

1. **Create resources**

   * S3 bucket `findmypet-uploads`
   * DynamoDB table `PetReports`  
     &nbsp;&nbsp;• PK `reportId` (string)  
     &nbsp;&nbsp;• SK `type_timestamp` (string)
   * Cognito user‑pool + hosted UI
   * API Gateway (REST) with a single Lambda proxy resource   
     ↳ point to the uploaded `lambda_function.py`

2. **Configure Lambda environment**

   | Key            | Value                    |
   |----------------|--------------------------|
   | `UPLOAD_BUCKET`| findmypet-uploads        |
   | `TABLE_NAME`   | PetReports               |
   | `REKOG_REGION` | *your Rekognition region*|

3. **Host the SPA**

   Put `frontend/index.html` on S3 + CloudFront **or** any static host.  
   Update the file with your own Cognito pool id, API endpoint and CloudFront
   URL.

That’s it – open the site, log in, upload two identical pet photos (one *lost*,
one *found*) and watch them turn **MATCHED**!

