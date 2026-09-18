$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$desk = [Environment]::GetFolderPath('Desktop')
$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desk '상장회사 분석.lnk'))
$s.TargetPath = Join-Path $dir 'run.bat'
$s.WorkingDirectory = $dir
$s.WindowStyle = 7
$s.IconLocation = "$env:SystemRoot\System32\imageres.dll,144"
$s.Description = '상장회사 재무분석 앱 실행'
$s.Save()
Write-Host ''
Write-Host '바탕화면에 [상장회사 분석] 아이콘을 만들었어요.'
