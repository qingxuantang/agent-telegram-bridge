# MIT License — see ../../LICENSE. Replace C:\Users\<user> with the deploy user.
# smoke-test.ps1 — WinPC Codex smoke test (first turn + resume + palace recall).
# Sets CODEX_API_KEY from codex.env (0600), runs codex exec, captures the
# thread_id from the newest session file, resumes it, and asks a palace
# recall question. Never prints the API key.
$codexEnv = "C:\Users\<user>\.codex\tg\codex.env"
$apiKey = $null
foreach ($line in Get-Content $codexEnv) {
    if ($line -match '^CODEX_API_KEY=(.*)$') { $apiKey = $Matches[1].Trim() }
}
if (-not $apiKey) { Write-Error "CODEX_API_KEY not found"; exit 1 }
$env:CODEX_API_KEY = $apiKey
$env:PATH = "C:\Users\<user>\AppData\Roaming\npm;$env:PATH"
$codex = "C:\Users\<user>\AppData\Roaming\npm\codex.cmd"
$workdir = "C:\Users\<user>"
$smokeDir = "C:\Users\<user>\codex-tg\smoke"
New-Item -ItemType Directory -Force -Path $smokeDir | Out-Null

Write-Output "=== TURN 1 (first turn, write-access test) ==="
$prompt1 = "你是一台 Windows 电脑上的 Codex。请用中文简短回答：你是谁？然后在 C:\Users\<user>\codex-tg\smoke\ 目录下创建一个名为 smoke-test.txt 的文件，内容只有一行：codex winpc smoke ok"
$null | & $codex exec --json --approve-for-me --skip-git-repo-check $prompt1 2>&1 | Out-File "$smokeDir\turn1.json" -Encoding utf8
Write-Output "--- turn1 reply (last 15 lines) ---"
Get-Content "$smokeDir\turn1.json" -Tail 15
Write-Output "--- smoke-test.txt exists? ---"
Get-Content "C:\Users\<user>\codex-tg\smoke\smoke-test.txt" -ErrorAction SilentlyContinue

Write-Output "=== FIND THREAD ID (newest session file) ==="
$session = Get-ChildItem "C:\Users\<user>\.codex\sessions" -Recurse -Filter "rollout-*.jsonl" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Output "newest session: $($session.FullName)"
$threadId = $null
if ($session.Name -match 'rollout-.*-([0-9a-f-]{36})\.jsonl') { $threadId = $Matches[1] }
Write-Output "thread_id: $threadId"
if (-not $threadId) { Write-Error "could not extract thread_id"; exit 2 }

Write-Output "=== TURN 2 (resume, test word 紫水晶) ==="
$prompt2 = "请用紫水晶开头回答：刚才让你创建的 smoke-test.txt 里写了什么？"
$null | & $codex exec resume $threadId --json --skip-git-repo-check $prompt2 2>&1 | Out-File "$smokeDir\turn2.json" -Encoding utf8
Write-Output "--- turn2 reply (last 15 lines) ---"
Get-Content "$smokeDir\turn2.json" -Tail 15

Write-Output "=== TURN 3 (palace recall) ==="
$prompt3 = "请查一下你的记忆宫殿（memory-palace）：WinPC 上 Claude Telegram daemon 的 bot 用户名是什么？只回答 bot 用户名和它所在的宫殿文件路径。"
$null | & $codex exec resume $threadId --json --skip-git-repo-check $prompt3 2>&1 | Out-File "$smokeDir\turn3.json" -Encoding utf8
Write-Output "--- turn3 reply (last 15 lines) ---"
Get-Content "$smokeDir\turn3.json" -Tail 15
Write-Output "=== SMOKE DONE ==="
