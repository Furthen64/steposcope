# Run from any directory while keeping relative STEP paths relative to the caller.
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is required. Install it and run 'uv sync' before launching steposcope."
    exit 127
}

& uv run --project $PSScriptRoot steposcope @args
exit $LASTEXITCODE

