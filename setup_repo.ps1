# setup_repo.ps1 - one-time bootstrap. Run from the nse-delivery-scanner folder.
# Chained with semicolons: PowerShell paste drops standalone `cd` lines.

$git = "C:\Program Files\Git\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }

# Windows Defender can KeyboardInterrupt during ensurepip -> skip, bootstrap manually.
python -m venv .venv --without-pip
.\.venv\Scripts\Activate.ps1
Invoke-WebRequest https://bootstrap.pypa.io/get-pip.py -OutFile get-pip.py; python get-pip.py; Remove-Item get-pip.py
python -m pip install -r requirements.txt

& $git init
& $git add .
& $git commit -m "nse-delivery-scanner: bhavcopy delivery ranking"
& $git branch -M main
& $git remote add origin https://github.com/rsireddy002/nse-delivery-scanner.git
& $git push -u origin main

Write-Host "`nNext: python precompute.py   (after 7 PM IST)" -ForegroundColor Green
