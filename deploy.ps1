# deploy.ps1
#
# À placer une fois pour toutes dans le dossier du repo
# (C:\Users\deryckel-l\Documents\tender-radar\project) et à relancer à chaque
# fois qu'il faut appliquer un zip et pousser. Élimine le besoin d'extraire
# dans un dossier temporaire puis de copier à la main -- c'était ça la source
# de toutes les erreurs (copie faite depuis le mauvais dossier).
#
# Usage :
#   .\deploy.ps1 -Zip "C:\Users\deryckel-l\Downloads\quelquechose.zip" -Message "mon message de commit"
#
# Sans -Zip, le script se contente de commit+push ce qui est déjà présent
# dans le dossier (utile si t'as édité des fichiers à la main).

param(
    [string]$Zip,
    [string]$Message = "Update from Claude"
)

$ErrorActionPreference = "Stop"

# --- 0. Vérifie qu'on est bien dans un repo git ---
if (-not (Test-Path ".git")) {
    Write-Error "Ce dossier n'est pas un repo git. Lance ce script depuis tender-radar\project."
    exit 1
}

# --- 1. Récupère l'état distant avant de toucher à quoi que ce soit ---
Write-Host "== git fetch + rebase (récupère les commits des bots avant de commencer) ==" -ForegroundColor Cyan
git fetch origin
git rebase origin/main

# --- 2. Applique le zip directement sur le repo, s'il y en a un ---
if ($Zip) {
    if (-not (Test-Path $Zip)) {
        Write-Error "Zip introuvable : $Zip"
        exit 1
    }
    Write-Host "== Extraction directe de $Zip sur le repo ==" -ForegroundColor Cyan
    Expand-Archive -Path $Zip -DestinationPath . -Force
}

# --- 3. Montre ce qui va être commité ---
Write-Host "== git status ==" -ForegroundColor Cyan
git status --short

$changes = git status --porcelain
if (-not $changes) {
    Write-Host "Rien à commiter. Si tu attendais des changements, vérifie que le zip contient bien les bons fichiers." -ForegroundColor Yellow
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
    Write-Host "Push rejeté -- un bot a probablement commité entre-temps. Rebase et nouvel essai." -ForegroundColor Yellow
    git fetch origin
    git rebase origin/main -X ours
    Start-Sleep -Seconds 3
}

Write-Error "Échec après $maxAttempts tentatives. Regarde le message d'erreur ci-dessus."
exit 1
