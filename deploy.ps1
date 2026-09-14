# deploy.ps1
#
# Peut être lancé de N'IMPORTE OU (il se place lui-même dans le bon dossier).
#
# Usage :
#   C:\Users\deryckel-l\Documents\tender-radar\project\deploy.ps1 -Zip "C:\Users\deryckel-l\Downloads\xxx.zip" -Message "mon message"
#   C:\Users\deryckel-l\Documents\tender-radar\project\deploy.ps1 -Message "mon message"   (sans zip, commit ce qui est déjà là)

param(
    [string]$Zip,
    [string]$Message = "Update from Claude"
)

$ErrorActionPreference = "Stop"

# --- 0. Se place TOUJOURS dans le bon dossier, peu importe d'où on lance le script ---
$RepoPath = "C:\Users\deryckel-l\Documents\tender-radar\project"
Set-Location $RepoPath
Write-Host "== Dossier actif : $(Get-Location) ==" -ForegroundColor DarkGray

if (-not (Test-Path ".git")) {
    Write-Error "$RepoPath n'est pas (ou plus) un repo git. Vérifie le chemin en haut du script."
    exit 1
}

# --- 1. Récupère l'état distant avant de toucher à quoi que ce soit ---
Write-Host "== git fetch + rebase ==" -ForegroundColor Cyan
git fetch origin
git rebase origin/main

# --- 2. Applique le zip directement sur le repo, s'il y en a un ---
if ($Zip) {
    if (-not (Test-Path $Zip)) {
        Write-Error "Zip introuvable : $Zip"
        exit 1
    }
    Write-Host "== Extraction directe de $Zip sur $RepoPath ==" -ForegroundColor Cyan
    Expand-Archive -Path $Zip -DestinationPath $RepoPath -Force
}

# --- 3. Montre ce qui va être commité ---
Write-Host "== git status ==" -ForegroundColor Cyan
git status --short

$changes = git status --porcelain
if (-not $changes) {
    Write-Host "Rien à commiter." -ForegroundColor Yellow
    exit 0
}

# --- 4. Commit ---
git add -A
git commit -m $Message

# --- 5. Push, avec retry automatique en cas de commit bot entre-temps ---
$maxAttempts = 5
for ($i = 1; $i -le $maxAttempts; $i++) {
    Write-Host "== git push (tentative $i/$maxAttempts) ==" -ForegroundColor Cyan
    git push
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Poussé avec succès." -ForegroundColor Green
        exit 0
    }
    Write-Host "Push rejeté -- rebase et nouvel essai." -ForegroundColor Yellow
    git fetch origin
    git rebase origin/main -X ours
    Start-Sleep -Seconds 3
}

Write-Error "Échec après $maxAttempts tentatives."
exit 1
