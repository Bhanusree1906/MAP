# Agentic Pipeline — Setup Guide


## GitHub OAuth

Configure a GitHub OAuth App once for the backend. Set its callback URL to
`http://localhost:8000/api/github/oauth/callback` for local development, then
set these environment variables before starting the backend:

```text
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
GITHUB_OAUTH_SCOPES=repo
```

Users can connect one or more GitHub accounts from `Connections`. Each account's
access token is encrypted with the same `SECRET_ENCRYPTION_KEY` mechanism used
for Codex credentials and is never returned to the browser. Each project selects
which authorized GitHub account should clone and push its repository.


## 1. Clone the Repository

Clone the project from GitHub:

```bash
git clone https://github.com/Appstrail-Technology/Agentic-Pipeline.git
```

Navigate to branch dev/shishira/agenticpipelinev1

Navigate into the project directory:

```bash
cd Agentic-Pipeline
```

---

## 2. Docker Setup

### Prerequisite

Make sure **Docker** is installed and running on your system.

Verify the Docker installation:

```bash
docker --version
```

### Build Sandbox Docker Image

Run the following command from the project root:

```bash
docker build --no-cache -t agent-sandbox:latest -f sandbox/Dockerfile sandbox/
```

### Build Salesforce Sandbox Docker Image

Run:

```bash
docker build --no-cache -t agent-sandbox-salesforce:latest -f sandbox/salesforce.Dockerfile .
```

After the builds complete, verify that the Docker images were created:

```bash
docker images
```

You should see:

```text
agent-sandbox:latest
agent-sandbox-salesforce:latest
```

---

## Run the Published Pipeline Image

The pipeline image is published separately from the sandbox images. Do not
put `.env` in a Docker image: it contains OAuth credentials, API keys, and
the encryption key for stored connections.

Pull all three images:

```bash
docker pull harikrishna1312/agent-pipeline-demo-final:latest
docker pull harikrishna1312/agent-sandbox-2:latest
docker pull harikrishna1312/agent-sandbox-salesforce-2:latest
```

Copy `.env.example` to `.env`, set the required secrets, then run:

```bash
docker compose up -d
```

The Compose file keeps the database and skill-package files in the
`pipeline-data` volume (`/data`). Keep this volume when recreating or updating
the container; `docker compose down -v` deletes it, including your database
and stored data. If skills were published before this storage-path change,
their old package files are not in the volume; restore those files from a
backup or republish the skills as new versions after updating.

The pipeline container listens on `http://localhost:8000`. It creates a
sandbox container only when a Salesforce bootstrap or pipeline run starts;
pulling or starting the pipeline image does not run both sandboxes
continuously. Docker Desktop must be running because Compose mounts the Docker
socket into the pipeline container.

To publish a new pipeline image after changing this repository:

```bash
docker build -t harikrishna1312/agent-pipeline-demo-final:latest .
docker push harikrishna1312/agent-pipeline-demo-final:latest
```

---

## 3. Frontend Setup

### Prerequisite

The frontend requires:

```text
Node.js 22
```

Verify your Node.js version:

```bash
node --version
```

The version should be **22.x.x**.

### Navigate to the Frontend Directory

```bash
cd frontend
```

### Install Dependencies

For the first-time setup, run:

```bash
npm install
```

> `npm install` is generally required only during the initial setup or whenever project dependencies change.

### Start the Frontend

Whenever you want to start the frontend application, run:

```bash
npm run dev
```

The frontend application runs at:

```text
http://localhost:5173/
```

---

## 4. Backend Setup

### Prerequisite

The backend requires:

```text
Python 3.11
```

Verify the Python version:

```bash
python --version
```

or:

```bash
python3 --version
```

The version should be **Python 3.11.x**.

### Create a Virtual Environment

Create a Python virtual environment:

```bash
python -m venv myenv
```

### Activate the Virtual Environment

#### macOS / Linux

```bash
source myenv/bin/activate
```

#### Windows — Command Prompt

```bash
myenv\Scripts\activate
```

#### Windows — PowerShell

```powershell
.\myenv\Scripts\Activate.ps1
```

Once activated, your terminal should display something similar to:

```text
(myenv)
```

### Install Backend Dependencies

Install all required Python packages:

```bash
pip install -r requirements.txt
```

---

## 5. Initialize the Database

After installing the backend dependencies and activating the virtual environment, initialize the database:

```bash
python init_db.py
```

Make sure the command completes successfully before continuing.

---

## 6. Open the Application

Start the frontend if it is not already running:

```bash
cd frontend
npm run dev
```

Open the following URL in your browser:

```text
http://localhost:5173/
```

Once the application opens, go to the **Connections** section to configure the required integrations.

---

# Connections Setup

## 7. Jira Connection

Go to:

```text
Application → Connections
```

Select the **Jira** connection.

### Step 1: Enter Connection Details

Provide a name for your Jira connection.

Example:

```text
My Jira Connection
```

Enter the **Project Key** of the Jira project you want to connect.

For example:

```text
ABC
```

where `ABC` is your Jira project key.

### Step 2: Connect Jira

Click the option to connect Jira.

You will be redirected to the Jira authorization page.

### Step 3: Grant Permission

On the Jira authorization page:

1. Review the requested permissions.
2. Click **Accept** or the corresponding authorization button.
3. After permission is granted, you will be redirected back to the application.

The Jira connection should now be successfully configured.

---

## 8. Codex Connection

Before connecting Codex from the application, enable device code authorization in your ChatGPT account.

### Step 1: Enable Device Code Authorization

Open your ChatGPT account.

Navigate to:

```text
Settings
   ↓
Security
   ↓
Login
```

Enable:

```text
Device Code Authorization for Codex
```

### Step 2: Connect Codex from the Application

Return to the Agentic Pipeline application:

```text
http://localhost:5173/
```

Go to:

```text
Connections → Codex
```

Click:

```text
Connect Codex
```

### Step 3: Log In

Log in using your ChatGPT account.

The Agentic Pipeline UI will display a **device code**.

Enter the device code on the authorization page.

After successful authorization, Codex will be connected to the application.

---

## 9. Salesforce Connection

The Salesforce integration requires a Salesforce authentication URL.

Make sure Salesforce CLI is installed and available from your terminal.

### Step 1: Log In to the Salesforce Organization

Run:

```bash
sf org login web --alias newDevOrg
```

This command opens a browser and asks you to log in to your Salesforce organization.

After successful authentication, the Salesforce organization will be saved using the alias:

```text
newDevOrg
```

### Step 2: Get the Salesforce Authentication URL

Run:

```bash
sf org auth show-sfdx-auth-url --target-org newDevOrg
```

This command returns the Salesforce authentication URL for the connected organization.

Copy the generated URL.

### Step 3: Connect Salesforce

Return to the application:

```text
http://localhost:5173/
```

Navigate to:

```text
Connections → Salesforce
```

Paste the Salesforce authentication URL generated by:

```bash
sf org auth show-sfdx-auth-url --target-org newDevOrg
```

Click the **Connect** button.

Salesforce should now be connected successfully.

---

# Complete Setup Flow

The complete setup sequence is:

### 1. Clone Repository

```bash
git clone https://github.com/Appstrail-Technology/Agentic-Pipeline.git
cd Agentic-Pipeline
```

### 2. Build Docker Images

```bash
docker build --no-cache -t agent-sandbox:latest -f sandbox/Dockerfile sandbox/

docker build --no-cache -t agent-sandbox-salesforce:latest -f sandbox/salesforce.Dockerfile .
```

### 3. Set Up Backend

```bash
python -m venv myenv
```

Activate the environment.

**macOS / Linux:**

```bash
source myenv/bin/activate
```

**Windows:**

```bash
myenv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Initialize the database:

```bash
python init_db.py
```

### 4. Set Up Frontend

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173/
```

### 5. Configure Connections

Configure the following connections from the **Connections** page:

```text
Jira
Codex
Salesforce
```

---

# Prerequisites Summary

| Dependency               | Required Version / Requirement     |
| ------------------------ | ---------------------------------- |
| Git                      | Latest recommended                 |
| Docker                   | Installed and running              |
| Node.js                  | 22                                 |
| Python                   | 3.11                               |
| Salesforce CLI           | Required for Salesforce connection |
| Jira Account             | Required for Jira authorization    |
| ChatGPT Account          | Required for Codex authorization   |
| Salesforce Account / Org | Required for Salesforce connection |

---

# Day-to-Day Development

After the initial setup, you generally do not need to reinstall all dependencies or rebuild everything unless there are project changes.

## Docker should be kept open in the system

## Start Frontend

```bash
cd frontend
npm run dev
```

Open:

```text
http://localhost:5173/
```

## Activate Backend Virtual Environment

### macOS / Linux

```bash
source myenv/bin/activate
```

### Windows

```bash
myenv\Scripts\activate
```

## Start the Backend

```bash
uvicorn backend.main:app --reload
```

> **Run a single worker only.** Pipeline run state (in-flight tasks, pause/
> cancel controls, the concurrency queue) lives in-process. Running multiple
> uvicorn workers would give each worker its own copy, so pause/cancel and the
> run cap would silently misbehave. Multi-worker support comes with the job
> queue in a later phase.

---

# Connection Summary

| Connection | Required Information                                   |
| ---------- | ------------------------------------------------------ |
| Jira       | Connection name and Jira Project Key                   |
| Codex      | ChatGPT account with Device Code Authorization enabled |
| Salesforce | SFDX authentication URL generated using Salesforce CLI |

### Jira

```text
Connections
→ Jira
→ Enter Connection Name
→ Enter Project Key
→ Connect
→ Accept Jira Permissions
```

### Codex

```text
ChatGPT Settings
→ Security
→ Login
→ Enable Device Code Authorization for Codex

Application
→ Connections
→ Codex
→ Connect Codex
→ Login
→ Enter Device Code
```

### Salesforce

```bash
sf org login web --alias newDevOrg
sf org auth show-sfdx-auth-url --target-org newDevOrg
```

Then:

```text
Application
→ Connections
→ Salesforce
→ Paste Salesforce Authentication URL
→ Connect
```



## Run with Docker
Build 3 image
Pull the required Docker images from Docker Hub:

```bash
1) docker build --no-cache -t agent-sandbox:latest -f sandbox/Dockerfile sandbox/
2) docker build --no-cache -t agent-sandbox-salesforce:latest -f sandbox/salesforce.Dockerfile .

After this build the main docker for pipeline
3) docker build -t harikrishna1312/agent-pipeline:latest .

```

Start the Agent Pipeline container:

```bash
docker run -d \
  --name agent-pipeline \
  --restart unless-stopped \
  --env-file .env \
  -e DATABASE_URL=sqlite:////data/agent_factory.db \
  -p 8000:8000 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v pipeline-data:/data \
  harikrishna1312/agent-pipeline:latest
```

Once the container is running, open the application in your browser:

**http://127.0.0.1:8000**

### Verify the container

Check that the Agent Pipeline container is running:

```bash
docker ps
```

To view the application logs:

```bash
docker logs -f agent-pipeline
```

To stop the container:

```bash
docker stop agent-pipeline
```

To start it again:

```bash
docker start agent-pipeline
```
