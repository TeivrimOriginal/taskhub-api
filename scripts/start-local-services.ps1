<#
.SYNOPSIS
    Поднимает локальные зависимости для тестов: PostgreSQL и поддельный Redis.

.DESCRIPTION
    На этой машине нет ни Docker, ни Redis, поэтому `docker compose up` не работает,
    а шесть тестов кэша и rate-limit молча пропускались. Скрипт поднимает ровно то,
    что нужно для полного зелёного прогона:

      * PostgreSQL 17.4 из D:\PostgreSQL\data2 на порту 5434 — тот порт, что записан
        в .env (postgresql+psycopg://postgres:postgres@127.0.0.1:5434/taskhub_test);
      * fakeredis.TcpFakeServer на 6379 — отдельный venv F:\.venvs\redisstub, проект
        не должен зависеть от fakeredis.

    Идемпотентен: если порт уже слушается, ничего не трогает и печатает «уже поднято».

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\start-local-services.ps1
    powershell -ExecutionPolicy Bypass -File scripts\start-local-services.ps1 -Stop

    ВАЖНО, если вызываешь из другой программы (CI-шаг, агент, пайплайн):
    перенаправляй вывод в файл, а не в конвейер powershell — `| Select-Object`, `| more`
    и подобное. Redis-заглушка живёт долго и держит унаследованный дескриптор вывода,
    поэтому конвейер ждёт конца потока и вызов выглядит зависшим, хотя обе службы уже подняты:

        powershell -ExecutionPolicy Bypass -File scripts\start-local-services.ps1 *> F:\tmp\svc.log
        Get-Content F:\tmp\svc.log -Tail 12
#>
[CmdletBinding()]
param(
    [int] $PostgresPort = 5434,
    [int] $RedisPort = 6379,
    [string] $PostgresBin = 'D:\PostgreSQL\pgsql\bin',
    [string] $PostgresData = 'D:\PostgreSQL\data2',
    [string] $PostgresLog = 'D:\PostgreSQL\pg2.log',
    [string] $RedisVenvPython = 'F:\.venvs\redisstub\Scripts\python.exe',
    [string] $PythonForCheck = 'python',
    [switch] $Stop
)

$ErrorActionPreference = 'Stop'

function Test-Port {
    param([string] $TargetHost, [int] $Port, [int] $TimeoutMs = 1500)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
        return ($async.AsyncWaitHandle.WaitOne($TimeoutMs) -and $client.Connected)
    }
    catch { return $false }
    finally { $client.Close() }
}

function Wait-Port {
    param([string] $TargetHost, [int] $Port, [int] $Seconds = 40)
    for ($i = 0; $i -lt $Seconds; $i++) {
        if (Test-Port -TargetHost $TargetHost -Port $Port) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

if ($Stop) {
    Write-Host 'Останавливаю PostgreSQL…'
    & "$PostgresBin\pg_ctl.exe" -D $PostgresData stop -m fast 2>&1 | Out-Null
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like '*redis_stub.py*' } |
        ForEach-Object {
            Write-Host "Останавливаю redis stub (pid $($_.ProcessId))…"
            Stop-Process -Id $_.ProcessId -Force
        }
    Write-Host 'Готово.'
    exit 0
}

# ---------------------------------------------------------------- PostgreSQL
# Две ловушки, обе стоили времени:
#   * pg_ctl В КОНВЕЙЕРЕ PowerShell вешает команду навсегда — postmaster наследует
#     stdout. Поэтому здесь Start-Process без -Wait и опрос порта вместо ожидания.
#   * listen_addresses по умолчанию ('localhost') на этой машине binds только ::1,
#     а DSN использует 127.0.0.1 — интеграционные тесты молча уходят в пропуски.
#     Поэтому listen_addresses задаётся явно.
if (Test-Port -TargetHost '127.0.0.1' -Port $PostgresPort) {
    Write-Host "PostgreSQL: уже слушает 127.0.0.1:$PostgresPort"
}
else {
    Write-Host "PostgreSQL: поднимаю $PostgresData на порту $PostgresPort…"
    $args = @(
        '-D', $PostgresData,
        '-l', $PostgresLog,
        '-o', "`"-p $PostgresPort -c listen_addresses=127.0.0.1`"",
        'start'
    )
    Start-Process -FilePath "$PostgresBin\pg_ctl.exe" -ArgumentList $args -NoNewWindow `
        -RedirectStandardOutput "$env:TEMP\pg-start.out" -RedirectStandardError "$env:TEMP\pg-start.err"
    if (Wait-Port -TargetHost '127.0.0.1' -Port $PostgresPort) {
        Write-Host "PostgreSQL: готов на 127.0.0.1:$PostgresPort"
    }
    else {
        # Из неинтерактивного процесса (например, вложенный powershell -File) дочерние
        # процессы postmaster падают с 0xC0000142 — DLL не инициализируются без
        # интерактивной сессии. Поэтому подсказка точная, а не «попробуйте ещё раз».
        Write-Warning "PostgreSQL не поднялся из этого процесса."
        Write-Warning 'Почти всегда это 0xC0000142 в '$PostgresLog': дочерние процессы'
        Write-Warning 'postmaster не инициализируются вне интерактивной сессии.'
        Write-Warning 'Запусти в обычном окне PowerShell:'
        Write-Warning "  & '$PostgresBin\pg_ctl.exe' -D '$PostgresData' -l '$PostgresLog' -o `"-p $PostgresPort -c listen_addresses=127.0.0.1`" start"
    }
}

# ------------------------------------------------------------------- Redis
if (Test-Port -TargetHost '127.0.0.1' -Port $RedisPort) {
    Write-Host "Redis (stub): уже слушает 127.0.0.1:$RedisPort"
}
else {
    if (-not (Test-Path $RedisVenvPython)) {
        Write-Error "Нет $RedisVenvPython. Создай: python -m venv F:\.venvs\redisstub; F:\.venvs\redisstub\Scripts\pip install fakeredis"
    }
    Write-Host 'Redis (stub): поднимаю fakeredis TcpFakeServer…'
    Start-Process -FilePath $RedisVenvPython -ArgumentList '..\tools\redis_stub.py', '--port', $RedisPort `
        -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
        -RedirectStandardOutput "$env:TEMP\redis-stub.out" -RedirectStandardError "$env:TEMP\redis-stub.err"
    if (Wait-Port -TargetHost '127.0.0.1' -Port $RedisPort) {
        Write-Host "Redis (stub): готов на 127.0.0.1:$RedisPort"
    }
    else {
        Write-Error "Redis stub не поднялся. Смотри $env:TEMP\redis-stub.err"
    }
}

Write-Host ''
Write-Host 'Проверка:'
& $PythonForCheck -c "import redis;r=redis.Redis(host='127.0.0.1',port=6379,socket_connect_timeout=3);print('  redis PING ->', r.ping())"
& "$PostgresBin\psql.exe" -h 127.0.0.1 -p $PostgresPort -U postgres -d taskhub_test -tAc 'select version()' |
    ForEach-Object { Write-Host "  postgres -> $_" }
Write-Host ''
Write-Host 'Теперь: pytest -q'