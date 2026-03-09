# Tribuna Scraper Setup Script
# PowerShell setup for Windows

Write-Host "=== Tribuna Scraper Setup ===" -ForegroundColor Green
Write-Host ""

# Check Python version
Write-Host "Checking Python version..." -ForegroundColor Yellow
$pythonVersion = & python --version 2>&1
Write-Host $pythonVersion

if ($pythonVersion -notmatch "Python 3.1[2-9]") {
    Write-Host "ERROR: Python 3.12+ required" -ForegroundColor Red
    exit 1
}

# Create virtual environment
Write-Host ""
Write-Host "Creating virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv") {
    Write-Host "Virtual environment already exists, skipping creation."
} else {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment" -ForegroundColor Red
        exit 1
    }
}

# Activate virtual environment
Write-Host ""
Write-Host "Activating virtual environment..." -ForegroundColor Yellow
& .\.venv\Scripts\Activate.ps1

# Upgrade pip
Write-Host ""
Write-Host "Upgrading pip..." -ForegroundColor Yellow
python -m pip install --upgrade pip

# Install dependencies
Write-Host ""
Write-Host "Installing dependencies from requirements.txt..." -ForegroundColor Yellow
Write-Host "(Scrapy 2.13+, NO Playwright)" -ForegroundColor Cyan
python -m pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: Failed to install dependencies" -ForegroundColor Red
    exit 1
}

# Verify installation
Write-Host ""
Write-Host "Verifying installation..." -ForegroundColor Yellow
$scrapyVersion = & python -m pip show scrapy | Select-String -Pattern "^Version:"
Write-Host $scrapyVersion

# Check Scrapy CLI
Write-Host ""
Write-Host "Testing Scrapy CLI..." -ForegroundColor Yellow
$spiders = & python -m scrapy list 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Available spiders:" -ForegroundColor Green
    $spiders | ForEach-Object { Write-Host "  - $_" }
} else {
    Write-Host "ERROR: Scrapy CLI test failed" -ForegroundColor Red
    exit 1
}

# Success
Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Run a spider: scrapy crawl fribourg -a days=7"
Write-Host "  2. Run tests: pytest tests/"
Write-Host "  3. Read docs: README.md, README_LOGIQUE.md, README_DATES.md"
Write-Host ""
