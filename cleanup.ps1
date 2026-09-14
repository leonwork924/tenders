# cleanup.ps1 - run once from the project folder to remove everything
# that the "leads" feature and the abandoned global corporate-discovery
# mode left behind. Safe to run multiple times (skips what's already gone).

$RepoPath = "C:\Users\deryckel-l\Documents\tender-radar\project"
Set-Location $RepoPath

Write-Host "== Removing leads feature ==" -ForegroundColor Cyan
Remove-Item ".github\workflows\leads.yml" -ErrorAction SilentlyContinue
Remove-Item "leads" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "site\prospects.json" -ErrorAction SilentlyContinue

Write-Host "== Removing 470MB orphaned corporate/ export (old global-discovery mode) ==" -ForegroundColor Cyan
Remove-Item "site\corporate" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item "corporate\src\export_by_country.py" -ErrorAction SilentlyContinue
Remove-Item "corporate\src\export_stats.py" -ErrorAction SilentlyContinue

Write-Host "== Resetting corporate_contacts.json to a clean empty state ==" -ForegroundColor Cyan
'{
  "generated": null,
  "count": 0,
  "companies": []
}' | Set-Content "site\corporate_contacts.json"

Write-Host "Done. Run git status to see what changed, then deploy.ps1 to commit+push." -ForegroundColor Green
