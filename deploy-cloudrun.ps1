<#
  Deploy FINN to Google Cloud Run (Mumbai) straight from this folder. Nothing goes to GitHub.

  One-time setup (about 10 minutes):
    1. Install the Google Cloud CLI:   winget install Google.CloudSDK   (then open a new terminal)
    2. Sign in:                        gcloud auth login
    3. Create a NEW project for FINN at https://console.cloud.google.com/projectcreate and link a billing
       account to it. Keep it separate from the project that owns your Gemini key - enabling billing on
       that one would move the key from the free tier to paid usage.

  Deploy (and redeploy after any change):
    powershell -ExecutionPolicy Bypass -File .\deploy-cloudrun.ps1 -Project <your-project-id>

  Your Gemini key is read from backend\.env and stored in Secret Manager; it is never printed or baked
  into the image. The service is capped at one instance, a per-IP rate limit and a daily AI-call budget.
#>
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [string]$Region = "asia-south1",
    [string]$Service = "finn",
    [int]$DailyAiBudget = 400,
    [int]$RateLimit = 150
)
$ErrorActionPreference = "Continue"   # gcloud reports progress on stderr; exit codes are checked instead
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Fail([string]$msg) { Write-Host $msg -ForegroundColor Red; exit 1 }
function Invoke-GCloud {
    & gcloud @args
    if ($LASTEXITCODE -ne 0) { Fail "gcloud $($args[0]) $($args[1]) failed (see the message above)." }
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Fail "Google Cloud CLI not found. Install it (winget install Google.CloudSDK), open a new terminal, run 'gcloud auth login'."
}
$account = & gcloud auth list --filter="status:ACTIVE" --format="value(account)" 2>$null
if (-not $account) { Fail "Not signed in to Google Cloud. Run: gcloud auth login" }
if (-not (Test-Path frontend\dist\index.html)) { Fail "frontend\dist is missing. Build it first: cd frontend; npm ci; npm run build" }

Step "Project $Project (signed in as $account)"
$number = & gcloud projects describe $Project --format="value(projectNumber)" 2>$null
if (-not $number) { Fail "Can't open project '$Project'. Check the id with: gcloud projects list" }

Step "Enabling Cloud Run, Cloud Build, Artifact Registry and Secret Manager (first run takes a few minutes)"
& gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com `
    secretmanager.googleapis.com compute.googleapis.com --project $Project
if ($LASTEXITCODE -ne 0) {
    Fail "Couldn't enable the APIs. Is a billing account linked? https://console.cloud.google.com/billing/linkedaccount?project=$Project"
}

$sa = "$number-compute@developer.gserviceaccount.com"
Step "Allowing Cloud Build to build the container"
$granted = & gcloud projects get-iam-policy $Project --flatten="bindings[].members" `
    --filter="bindings.role:roles/run.builder AND bindings.members:$sa" --format="value(bindings.role)" 2>$null
if (-not $granted) {
    Invoke-GCloud projects add-iam-policy-binding $Project --member="serviceAccount:$sa" --role="roles/run.builder" `
        --condition=None --quiet | Out-Null
    Write-Host "Waiting 60 seconds for the permission to take effect..."
    Start-Sleep -Seconds 60
}

$secretArgs = @()
$keyLine = Get-Content backend\.env -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\s*GEMINI_API_KEY\s*=\s*\S' } | Select-Object -First 1
if ($keyLine) {
    Step "Storing your Gemini key in Secret Manager (never printed, never in the image)"
    $key = ($keyLine -replace '^\s*GEMINI_API_KEY\s*=\s*', '').Trim().Trim('"').Trim("'")
    $current = & gcloud secrets versions access latest --secret=finn-llm-key --project $Project 2>$null
    if ($LASTEXITCODE -ne 0 -or "$current".Trim() -ne $key) {
        $tmp = [IO.Path]::GetTempFileName()
        try {
            [IO.File]::WriteAllText($tmp, $key)
            & gcloud secrets describe finn-llm-key --project $Project --format="value(name)" 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                Invoke-GCloud secrets versions add finn-llm-key --data-file="$tmp" --project $Project | Out-Null
            } else {
                Invoke-GCloud secrets create finn-llm-key --replication-policy=automatic --data-file="$tmp" --project $Project | Out-Null
            }
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    Invoke-GCloud secrets add-iam-policy-binding finn-llm-key --member="serviceAccount:$sa" `
        --role="roles/secretmanager.secretAccessor" --condition=None --project $Project --quiet | Out-Null
    $secretArgs = @("--set-secrets", "GEMINI_API_KEY=finn-llm-key:latest")
} else {
    Write-Host "No GEMINI_API_KEY in backend\.env: deploying in rules mode (cached AI analyses are still shown)." -ForegroundColor Yellow
}

Step "Snapshotting the local cache into the image"
$py = if (Test-Path backend\.venv\Scripts\python.exe) { "backend\.venv\Scripts\python.exe" } `
      elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
& $py deploy\make_seed.py
if ($LASTEXITCODE -ne 0) { Fail "Couldn't snapshot the cache." }

Step "Building and deploying to Cloud Run in $Region (about 3-6 minutes)"
Invoke-GCloud run deploy $Service --source . --region $Region --project $Project --allow-unauthenticated `
    --memory 1Gi --cpu 1 --max-instances 1 --concurrency 20 --timeout 600 --cpu-boost `
    --set-env-vars "FINN_NO_BACKGROUND=1,FINN_KEEP_FILES=0,FINN_RATE_LIMIT=$RateLimit,LLM_DAILY_BUDGET=$DailyAiBudget" `
    @secretArgs --quiet

$url = & gcloud run services describe $Service --region $Region --project $Project --format="value(status.url)"
Write-Host "`nFINN is live: $url" -ForegroundColor Green
Write-Host "Open it on your phone. If the brief shows an amber 'exchange feed' note, NSE is blocking the cloud server and FINN is showing the latest data from your snapshot."
