#!/usr/bin/env bash
# Launch OpenAMASE with the Goheung scenario and the TCP bridge on 5555.
#
# The standard config/amase Plugins.xml already opens TCPServer Port="5555"
# (see OpenAMASE/OpenAMASE/config/amase/Plugins.xml) which is exactly the
# port uxas_search_listener.py mirrors MissionCommand into. Run AMASE,
# then start the listener — when UxAS plans, AMASE will draw it.
#
# Override AMASE location with AMASE_HOME=<path>  (default: the in-tree
# clone next to OpenUxAS).
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# Default to the Gangnam scenario (matches vehicles.json home). Override with
# SCENARIO=<abs path> to use Goheung (Scenario_Goheung.xml) or another area.
SCENARIO="${SCENARIO:-${HERE}/Scenario_Gangnam.xml}"

AMASE_HOME=${AMASE_HOME:-/home/swerc/myclaude/OpenUxAS/OpenAMASE/OpenAMASE}
if [[ ! -d "$AMASE_HOME" ]]; then
  echo "ERROR: AMASE_HOME=$AMASE_HOME does not exist." >&2
  echo "       Set AMASE_HOME to the directory containing dist/OpenAMASE.jar." >&2
  exit 1
fi
if [[ ! -f "$AMASE_HOME/dist/OpenAMASE.jar" ]]; then
  echo "WARNING: $AMASE_HOME/dist/OpenAMASE.jar not found." >&2
  echo "         Run 'ant jar' inside $AMASE_HOME first." >&2
fi
if [[ ! -f "$SCENARIO" ]]; then
  echo "ERROR: Scenario file missing: $SCENARIO" >&2
  exit 1
fi

echo "[amase] scenario : $SCENARIO"
echo "[amase] AMASE_HOME: $AMASE_HOME"
echo "[amase] TCP bridge: 127.0.0.1:5555 (from config/amase/Plugins.xml)"
echo "[amase] starting GUI ..."

cd "$AMASE_HOME"
exec java -Xmx2048m \
  -splash:./data/amase_splash.png \
  -classpath "./dist/*:./lib/*" \
  avtas.app.Application \
  --config config/amase \
  --scenario "$SCENARIO"
