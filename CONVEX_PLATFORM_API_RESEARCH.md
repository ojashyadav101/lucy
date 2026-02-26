# Convex Management API & Deployment Workflow Research

## Overview

This document provides a comprehensive guide to programmatically creating, deploying, and managing Convex projects using the Convex Management API and CLI tools.

---

## 1. Convex Management API

### Base URL
```
https://api.convex.dev/v1
```

### OpenAPI Specification
- Available at: `https://api.convex.dev/v1/openapi.json`
- Status: **Beta** - Contact `platforms@convex.dev` for additional capabilities

### Client Library
- NPM Package: `@convex-dev/platform`
- Provides a client wrapper for the Management API

---

## 2. Authentication Methods

### 2.1 Team Access Tokens
- **Use Case**: Managing your own team's projects
- **Format**: Bearer token in Authorization header
- **Header**: `Authorization: Bearer <token>`
- **Where to Get**: Convex Dashboard (Team Settings)
- **Scope**: Full control over team projects and deployments

### 2.2 OAuth Application Tokens
- **Use Case**: Integrations built on behalf of users
- **Format**: Bearer token in Authorization header
- **Header**: `Authorization: Bearer <token>`
- **Scope**: Team-scoped tokens authorize creating projects, deployments, and read/write access to project data

### 2.3 Deploy Keys
- **Use Case**: Non-interactive CLI authentication (CI/CD pipelines)
- **Format**: `Authorization: Convex <deploy_key>`
- **Types**:
  - **Production**: `prod:qualified-jaguar-123|eyJ2...0=`
  - **Preview**: `preview:team-slug:project-slug|eyJ2...0=`
  - **Dev**: `dev:joyful-jaguar-123|eyJ2...0=`
  - **Admin**: `bold-hyena-681|01c2...c09c` (complete control, works offline)
  - **Project Token**: `project:team-slug:project-slug|eyJ2...0=` (total project control)

### 2.4 Getting Team ID
- **Endpoint**: `GET /token_details`
- **Purpose**: Retrieves team ID from authentication token
- **Use Case**: Required for most Management API endpoints (team-scoped operations)

---

## 3. Management API Endpoints

### 3.1 Get Token Details
```
GET /token_details
Authorization: Bearer <token>
```
**Response**: Returns team ID and token metadata

### 3.2 List Projects
```
GET /teams/:team_id/list_projects
Authorization: Bearer <token>
```
**Path Parameters**:
- `team_id` (int64, required): Team ID

**Response**: Array of projects with project IDs

### 3.3 Create Project
```
POST /teams/:team_id/create_project
Authorization: Bearer <token>
Content-Type: application/json
```
**Path Parameters**:
- `team_id` (int64, required): Team ID

**Request Body**:
```json
{
  "projectName": "string",           // Required: Full project name
  "deploymentType": "dev" | "prod",  // Optional: Provision deployment
  "deploymentRegion": "string" | null, // Optional: Hosting region
  "deploymentClass": "string" | null   // Optional: Deployment class
}
```

**Response**:
```json
{
  "projectId": 0,                    // int64: Created project ID
  "deploymentName": "string" | null,  // e.g., "playful-otter-123"
  "deploymentUrl": "string" | null    // Cloud URL if deployment created
}
```

### 3.4 Create Deployment
```
POST /projects/:project_id/create_deployment
Authorization: Bearer <token>
Content-Type: application/json
```
**Path Parameters**:
- `project_id` (int64, required): Project ID

**Request Body**:
```json
{
  "type": "dev" | "prod",            // Required: Deployment type
  "reference": "string" | null,      // Optional: Unique identifier within project
  "region": "string" | null,         // Optional: Hosting region
  "class": "string" | null           // Optional: Deployment class
}
```

**Response**:
```json
{
  "id": 0,                            // Deployment ID
  "name": "string",                   // e.g., "playful-otter-123"
  "projectId": 0,
  "deploymentType": "dev" | "prod" | "preview" | "custom",
  "reference": "string",
  "region": "string",
  "kind": "cloud",
  "createTime": 0,                    // Timestamp in milliseconds
  "creator": 0,                       // Member ID
  "isDefault": true,
  "dashboardEditConfirmation": true,
  "previewIdentifier": "string" | null
}
```

### 3.5 Create Deploy Key
```
POST /projects/:project_id/create_deploy_key
Authorization: Bearer <token>
```
**Path Parameters**:
- `project_id` (int64, required): Project ID

**Note**: Endpoint details available in dashboard or via API

---

## 4. Deployment Platform API

### Base URL
```
https://<deployment-name>.convex.cloud/api/v1
```
Example: `https://happy-otter-123.convex.cloud/api/v1`

### Authentication
- **Format**: `Authorization: Convex <deploy_key>`
- **Supported Keys**: Deployment keys, Team Access Tokens, OAuth Application Tokens

### 4.1 List Environment Variables
```
GET /list_environment_variables
Authorization: Convex <token>
```
**Response**:
```json
{
  "environmentVariables": {
    "VAR_NAME": "value",
    ...
  }
}
```

### 4.2 Update Environment Variables
```
POST /update_environment_variables
Authorization: Convex <token>
Content-Type: application/json
```
**Request Body**:
```json
{
  "changes": [
    {
      "name": "VAR_NAME",      // Required: Max 40 chars, start with letter
      "value": "value" | null  // Max 8KB, null to delete
    }
  ]
}
```

**Constraints**:
- Maximum 100 environment variables per deployment
- Variable names: max 40 characters, must start with a letter, contain only letters, numbers, and underscores
- Variable values: max 8KB
- **Important**: Updating environment variables invalidates all subscriptions

---

## 5. Convex CLI Commands

### 5.1 Installation
```bash
npm install convex
```

### 5.2 Development Commands

#### Start Dev Server
```bash
npx convex dev
```
- Watches for changes to functions and schemas
- Automatically pushes updates to dev deployment
- Requires login or deploy key

#### Local Development
```bash
npx convex dev --local
```
- Runs against local deployment on your machine
- No account required (anonymous development)

#### One-time Dev Run
```bash
npx convex dev --once
```
- Runs dev server once and exits
- Useful for scripting/CI

### 5.3 Deployment Commands

#### Deploy to Production
```bash
npx convex deploy
```
- Deploys to production after confirming changes
- Requires production deploy key or login

#### Deploy with Deploy Key
```bash
CONVEX_DEPLOY_KEY='prod:qualified-jaguar-123|eyJ2...0=' npx convex deploy
```

### 5.4 Running Functions

#### Run Function
```bash
npx convex run <functionName> [args]
```
- Executes query, mutation, or action on dev deployment
- Options:
  - `--push`: Sync local code before running
  - `--prod`: Run against production
  - `--watch`: Live update query results

### 5.5 Authentication Commands

#### Login
```bash
npx convex login
```
- Creates account and links local projects
- Stores user token at `~/.convex/config.json`

#### Logout
```bash
npx convex logout
```
- Removes credentials from device

### 5.6 Other Useful Commands

```bash
npx convex dashboard          # Open Convex dashboard
npx convex docs               # Access documentation
npx convex logs               # Tail deployment logs
npx convex logs --prod        # Production logs
npx convex import --table <tableName> <path>  # Import data
npx convex export --path <directoryPath>      # Export data
npx convex data               # Display table data
```

---

## 6. Vercel REST API (Git-less Deployment)

### 6.1 Authentication
- **Endpoint**: `https://api.vercel.com`
- **Header**: `Authorization: Bearer <vercel_access_token>`
- **Token**: Obtain from Vercel Dashboard → Settings → Tokens

### 6.2 Step 1: Upload Files

**Endpoint**:
```
POST https://api.vercel.com/v2/files
```

**Headers**:
```
Authorization: Bearer <vercel_token>
Content-Type: application/json
x-vercel-digest: <SHA1_hash>        // Required: SHA1 hash of file
Content-Length: <file_size_bytes>   // Optional: File size
```

**Query Parameters** (Optional):
- `teamId`: Team identifier
- `slug`: Team slug

**Process**:
1. For each file, generate SHA1 hash:
   ```bash
   echo -n <filename> | shasum
   # or
   shasum <file>
   ```
2. Upload file content in request body
3. Response: `{ "urls": [] }` (empty if successful)

**Note**: Files can be uploaded multiple times (idempotent)

### 6.3 Step 2: Create Deployment

**Endpoint**:
```
POST https://api.vercel.com/v13/deployments
```

**Request Body**:
```json
{
  "name": "deployment-name",
  "files": [
    {
      "file": "/path/to/file",
      "sha": "<SHA1_hash>",
      "size": <bytes>
    }
  ],
  "projectSettings": {
    "framework": "nextjs",
    "buildCommand": "npm run build",
    "outputDirectory": ".next",
    "installCommand": "npm install"
  }
}
```

**Response**: Deployment object with deployment URL and status

### 6.4 Automation Script Example

```javascript
// Pseudo-code workflow
const files = getAllProjectFiles();
const uploadedFiles = [];

for (const file of files) {
  const sha = generateSHA1(file.content);
  const size = file.content.length;
  
  // Upload file
  await fetch('https://api.vercel.com/v2/files', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${vercelToken}`,
      'x-vercel-digest': sha,
      'Content-Length': size.toString(),
      'Content-Type': 'application/json'
    },
    body: file.content
  });
  
  uploadedFiles.push({
    file: file.path,
    sha: sha,
    size: size
  });
}

// Create deployment
await fetch('https://api.vercel.com/v13/deployments', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${vercelToken}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: 'my-deployment',
    files: uploadedFiles,
    projectSettings: { /* ... */ }
  })
});
```

---

## 7. Complete Workflow: Programmatic Project Creation & Deployment

### 7.1 Create Project with Deployment

```javascript
// 1. Get team ID from token
const tokenDetails = await fetch('https://api.convex.dev/v1/token_details', {
  headers: { 'Authorization': `Bearer ${teamToken}` }
});
const { teamId } = await tokenDetails.json();

// 2. Create project with production deployment
const projectResponse = await fetch(
  `https://api.convex.dev/v1/teams/${teamId}/create_project`,
  {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${teamToken}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      projectName: 'My New Project',
      deploymentType: 'prod',
      deploymentRegion: 'us-east-1' // Optional
    })
  }
);
const { projectId, deploymentName, deploymentUrl } = await projectResponse.json();

// 3. Create deploy key for CI/CD
const deployKeyResponse = await fetch(
  `https://api.convex.dev/v1/projects/${projectId}/create_deploy_key`,
  {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${teamToken}` }
  }
);
const { deployKey } = await deployKeyResponse.json();

// 4. Set environment variables
await fetch(`${deploymentUrl}/api/v1/update_environment_variables`, {
  method: 'POST',
  headers: {
    'Authorization': `Convex ${deployKey}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    changes: [
      { name: 'API_KEY', value: 'secret-value' },
      { name: 'ENVIRONMENT', value: 'production' }
    ]
  })
});

// 5. Deploy Convex functions
// In CI/CD pipeline:
process.env.CONVEX_DEPLOY_KEY = deployKey;
// Then run: npx convex deploy
```

### 7.2 Deploy to Vercel (Git-less)

```javascript
// 1. Build your Next.js/React app
// npm run build

// 2. Collect all files from build output
const files = collectFilesFromBuild('.next', 'public');

// 3. Upload files to Vercel
const fileRefs = [];
for (const file of files) {
  const sha = await generateSHA1(file.content);
  await uploadFileToVercel(file.content, sha);
  fileRefs.push({
    file: file.path,
    sha: sha,
    size: file.content.length
  });
}

// 4. Create Vercel deployment
const deployment = await createVercelDeployment({
  name: 'my-app',
  files: fileRefs,
  projectSettings: {
    framework: 'nextjs',
    buildCommand: 'npm run build',
    outputDirectory: '.next'
  }
});
```

---

## 8. Free Tier Limitations

### 8.1 Projects
- **Limit**: 20 projects per team

### 8.2 Function Calls
- **Limit**: 1,000,000 function calls per month
- **Includes**: Explicit client calls, scheduled executions, subscription updates, file accesses
- **Overage**: $2 per 1,000,000 additional calls
- **Tracking**: Team Settings → Usage in Convex dashboard

### 8.3 Concurrent Executions
- **Queries/Mutations/HTTP Actions**: 16 concurrent
- **V8 Actions**: 64 concurrent
- **Scheduled Jobs**: 10 concurrent

### 8.4 Action Execution
- **Limit**: 20 GiB-hours per month

### 8.5 Function Limits
- **Argument/Return Size**: 16 MiB each
- **Query/Mutation Execution Time**: 1 second
- **Action Execution Time**: 10 minutes

### 8.6 Environment Variables
- **Maximum**: 100 per deployment
- **Name Length**: Max 40 characters
- **Value Length**: Max 8KB

---

## 9. Professional Plan Comparison

### Function Calls
- **Free**: 1,000,000/month
- **Professional**: 25,000,000/month

### Concurrent Executions
- **Free**: 16 (queries/mutations), 64 (V8 actions), 10 (scheduled)
- **Professional**: Significantly higher limits

---

## 10. Best Practices & Notes

### 10.1 Deploy Key Security
- Never commit deploy keys to version control
- Use environment variables or secret management systems
- Rotate keys periodically
- Use preview deploy keys for non-production environments

### 10.2 Environment Variables
- Updating env vars invalidates all subscriptions (consider impact)
- Use deployment-specific keys for different environments
- Store sensitive values securely

### 10.3 Project Management
- Use project tokens for OAuth integrations
- Team tokens for internal automation
- Deploy keys for CI/CD pipelines

### 10.4 Deployment Workflow
1. Create project via Management API
2. Create deployment (dev/prod)
3. Generate deploy key
4. Set environment variables
5. Deploy code using CLI with deploy key
6. Monitor via dashboard or logs API

### 10.5 Vercel Integration
- Upload files before creating deployment
- Generate SHA1 hashes correctly (use file content, not filename)
- Include all required files in deployment
- Set appropriate project settings for framework

---

## 11. Resources & Support

### Documentation
- Management API: `https://docs.convex.dev/management-api`
- Deployment API: `https://docs.convex.dev/deployment-platform-api`
- CLI Docs: `https://docs.convex.dev/cli`
- OpenAPI Spec: `https://api.convex.dev/v1/openapi.json`

### Support
- **Email**: `platforms@convex.dev` (for Management API capabilities)
- **Community**: Discord (discord-questions.convex.dev)

### NPM Packages
- `@convex-dev/platform` - Management API client wrapper
- `convex` - CLI and client SDK

---

## 12. Example: Complete Platform Integration

```typescript
import { ConvexPlatformClient } from '@convex-dev/platform';

// Initialize client
const client = new ConvexPlatformClient({
  token: process.env.CONVEX_TEAM_TOKEN
});

// 1. Get team ID
const tokenDetails = await client.getTokenDetails();
const teamId = tokenDetails.teamId;

// 2. Create project
const project = await client.createProject(teamId, {
  projectName: 'User Project',
  deploymentType: 'prod'
});

// 3. Create deploy key
const deployKey = await client.createDeployKey(project.projectId);

// 4. Set environment variables
const deploymentClient = new ConvexDeploymentClient({
  deploymentUrl: project.deploymentUrl,
  token: deployKey
});

await deploymentClient.updateEnvironmentVariables([
  { name: 'API_KEY', value: 'value' }
]);

// 5. Deploy code (using CLI)
// Set CONVEX_DEPLOY_KEY and run: npx convex deploy

// 6. Deploy frontend to Vercel
// Use Vercel REST API to upload files and create deployment
```

---

## Summary

This research provides a complete guide for building a platform that programmatically:
1. ✅ Creates Convex projects via Management API
2. ✅ Creates deployments (dev/prod)
3. ✅ Sets environment variables programmatically
4. ✅ Authenticates using team tokens, OAuth tokens, or deploy keys
5. ✅ Deploys Convex code via CLI with deploy keys
6. ✅ Deploys frontend to Vercel without git using REST API
7. ✅ Understands free tier limitations (20 projects, 1M function calls/month)

All APIs are in Beta - contact `platforms@convex.dev` for production use cases requiring additional capabilities.
