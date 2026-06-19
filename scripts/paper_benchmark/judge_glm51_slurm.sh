#!/bin/bash
# Stage the 270Q Qwen/GPT/Gemma result files with unique names and judge them with
# GLM-5.1 via OpenRouter. Intended to run on ORCD; optionally pass a Slurm
# dependency string such as "afterok:14319509:14319510".
set -euo pipefail

DEPENDENCY="${1:-}"
RUN_ID="${GLM51_RUN_ID:-270q_run1}"
MODEL="${GLM51_MODEL:-z-ai/glm-5.1}"
STAGE_DIR="$HOME/bench_out/judge_inputs_270q_glm51"
OUT_DIR="$HOME/bench_out/judged"
REPO="$HOME/A2rchi"
SIF="$HOME/.archi-bundle-state/sif/archi-data-manager.sif"
SECRET="$HOME/.archi-bundle-state/bundle/secrets/archi/openrouter_api_key.txt"
INPUT_MANIFEST="${GLM51_INPUT_MANIFEST:-}"
EXPECTED_ROWS="${GLM51_EXPECTED_ROWS:-270}"
SOURCE_MODE="${GLM51_SOURCE_MODE:-auto}"
TIER_ARGS="${GLM51_TIER_ARGS:---allow-mixed-tiers}"

mkdir -p "$STAGE_DIR" "$OUT_DIR"
find "$STAGE_DIR" -maxdepth 1 \( -type l -o -type f \) -name '*.json' -delete

stage_one() {
  local label=$1
  local src=$2
  [ -f "$src" ] || { echo "ERROR: missing $src" >&2; exit 2; }
  ln -sfn "../${src#"$HOME/bench_out/"}" "$STAGE_DIR/${label}.json"
}

count_qids() {
  grep -o '"question_[0-9][0-9]*"[[:space:]]*:' "$1" | wc -l | tr -d ' '
}

stage_inputs() {
  if [ -n "$INPUT_MANIFEST" ]; then
    [ -f "$INPUT_MANIFEST" ] || { echo "ERROR: missing GLM51_INPUT_MANIFEST=$INPUT_MANIFEST" >&2; exit 2; }
    while IFS=$'\t' read -r label src; do
      case "${label:-}" in ""|\#*) continue ;; esac
      [ -n "${src:-}" ] || { echo "ERROR: manifest row for '$label' has no source path" >&2; exit 2; }
      stage_one "$label" "$src"
    done < "$INPUT_MANIFEST"
    return
  fi

  stage_one qwen35b_bare     "$HOME/bench_out/run_260q_orcd_v3_35b/results_v3_bare.json"
  stage_one qwen35b_rag      "$HOME/bench_out/run_260q_orcd_v3_35b/results_v3_rag.json"
  stage_one qwen35b_no-tools "$HOME/bench_out/run_260q_orcd_v3_35b/results_v3_no-tools.json"
  stage_one qwen35b_live     "$HOME/bench_out/run_260q_orcd_v3_35b/results_v3_live.json"

  stage_one qwen27b_bare     "$HOME/bench_out/run_260q_orcd_v3_27b/results_v3_bare.json"
  stage_one qwen27b_rag      "$HOME/bench_out/run_260q_orcd_v3_27b/results_v3_rag.json"
  stage_one qwen27b_no-tools "$HOME/bench_out/run_260q_orcd_v3_27b/results_v3_no-tools.json"
  stage_one qwen27b_live     "$HOME/bench_out/run_260q_orcd_v3_27b/results_v3_live.json"

  stage_one gpt55_bare       "$HOME/bench_out/run_270q_gpt55_openai/results_v3_bare.json"
  stage_one gpt55_rag        "$HOME/bench_out/run_270q_gpt55_openai/results_v3_rag.json"
  stage_one gpt55_no-tools   "$HOME/bench_out/run_270q_gpt55_openai/results_v3_no-tools.json"
  stage_one gpt55_live       "$HOME/bench_out/run_270q_gpt55_openai/results_v3_live.json"

  stage_one gemma4_31b_bare     "$HOME/bench_out/run_260q_orcd_v3_gemma4-31b/results_v3_bare.json"
  stage_one gemma4_31b_rag      "$HOME/bench_out/run_260q_orcd_v3_gemma4-31b/results_v3_rag.json"
  stage_one gemma4_31b_no-tools "$HOME/bench_out/run_260q_orcd_v3_gemma4-31b/results_v3_no-tools.json"
  stage_one gemma4_31b_live     "$HOME/bench_out/run_260q_orcd_v3_gemma4-31b/results_v3_live.json"

  stage_one gemma4_26b_bare     "$HOME/bench_out/run_260q_orcd_v3_gemma4-26b/results_v3_bare.json"
  stage_one gemma4_26b_rag      "$HOME/bench_out/run_260q_orcd_v3_gemma4-26b/results_v3_rag.json"
  stage_one gemma4_26b_no-tools "$HOME/bench_out/run_260q_orcd_v3_gemma4-26b/results_v3_no-tools.json"
  stage_one gemma4_26b_live     "$HOME/bench_out/run_260q_orcd_v3_gemma4-26b/results_v3_live.json"
}

submit_judge() {
  local dep_args=()
  if [ -n "$DEPENDENCY" ]; then
    dep_args=(--dependency="$DEPENDENCY")
  fi

  sbatch --parsable "${dep_args[@]}" <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-judge-glm51-270q
#SBATCH --output=$HOME/archi-judge-glm51-270q.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=96G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null

[ -s "$SECRET" ] || { echo "ERROR: missing $SECRET" >&2; exit 3; }
export OPENROUTER_API_KEY=\$(cat "$SECRET")

echo "=== preflight: staged inputs ==="
for f in "$STAGE_DIR"/*.json; do
  n=\$(grep -o '"question_[0-9][0-9]*"[[:space:]]*:' "\$f" | wc -l | tr -d ' ')
  printf '%-28s rows=%s\\n' "\$(basename "\$f")" "\$n"
  if [ "\$n" -ne "$EXPECTED_ROWS" ]; then
    echo "ERROR: refusing to judge \$(basename "\$f") with \$n rows" >&2
    exit 4
  fi
done

echo "=== judge: $MODEL run_id=$RUN_ID source_mode=$SOURCE_MODE tier_args=$TIER_ARGS ==="
apptainer exec \\
  --bind "$REPO:/workspace" \\
  --bind "$HOME/bench_out:/bench_out" \\
  --env OPENROUTER_API_KEY="\$OPENROUTER_API_KEY" \\
  --env PYTHONPATH=/workspace \\
  "$SIF" \\
  python3 /workspace/scripts/run_evaluation.py \\
    --input-dir /bench_out/judge_inputs_270q_glm51 \\
    --output-dir /bench_out/judged \\
    --model "$MODEL" \\
    --run-id "$RUN_ID" \\
    --retry-errors \\
    --source-mode "$SOURCE_MODE" \\
    $TIER_ARGS
SBATCH
}

stage_inputs
echo "Staged inputs in $STAGE_DIR:"
for f in "$STAGE_DIR"/*.json; do
  printf '%-28s rows=%s -> %s\n' "$(basename "$f")" "$(count_qids "$f")" "$(readlink "$f")"
done

jid=$(submit_judge)
echo "judge_jid=$jid"
squeue -j "$jid" -o "%i %j %T %M %R"
