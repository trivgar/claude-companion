#!/usr/bin/env bash
# Train one GPT-SoVITS v2 voice from a dataset made by prep.py.
#
#   GPT_SOVITS_DIR=~/GPT-SoVITS ./train.sh [--dry-run] <voice> <dataset-dir>
#
# <dataset-dir> holds <voice>.list and wavs/ (prep.py's output). Results go to
#   ${VOICES_DIR:-~/.local/share/claude-companion/voices}/<voice>/
# Tunables (env): S1_EPOCHS (15), S2_EPOCHS (8), BATCH (auto from VRAM: 4/8/12),
# GPU (0). Run it inside the conda env you installed
# GPT-SoVITS into. Stage file names follow upstream v2 (see README); if a
# newer upstream renamed one, the stage fails loudly and --dry-run shows it.
set -euo pipefail

DRY=0
if [ "${1:-}" = "--dry-run" ]; then DRY=1; shift; fi
VOICE="${1:-}"; DATA="${2:-}"
if [ -z "$VOICE" ] || [ -z "$DATA" ]; then
  echo "usage: GPT_SOVITS_DIR=... $0 [--dry-run] <voice> <dataset-dir>" >&2; exit 2
fi
if [ -z "${GPT_SOVITS_DIR:-}" ]; then
  echo "GPT_SOVITS_DIR is required: the folder you cloned GPT-SoVITS into" >&2; exit 2
fi
case "$VOICE" in *[!A-Za-z0-9_-]*) echo "voice name: letters, digits, _ and - only" >&2; exit 2;; esac

G="$GPT_SOVITS_DIR"
OUT="${VOICES_DIR:-$HOME/.local/share/claude-companion/voices}/$VOICE"
EXP="$OUT/exp"
LIST="$DATA/$VOICE.list"
WAVS="$DATA/wavs"
GPU="${GPU:-0}"
S1_EPOCHS="${S1_EPOCHS:-15}"
S2_EPOCHS="${S2_EPOCHS:-8}"
PRE="$G/GPT_SoVITS/pretrained_models"
V2="$PRE/gsv-v2final-pretrained"
BERT="$PRE/chinese-r*-wwm-ext-large"   # upstream's BERT folder; the glob finds it
BERT=$(compgen -G "$BERT" | head -1 || true); BERT="${BERT:-$PRE/bert-folder-not-found}"
HUBERT="$PRE/chinese-hubert-base"
S2G="$V2/s2G2333k.pth"; S2D="$V2/s2D2333k.pth"
S1="$V2/s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"

if [ -z "${BATCH:-}" ]; then
  MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits -i "$GPU" 2>/dev/null | head -1 || true)
  MB="${MB:-12000}"
  if [ "$MB" -ge 22000 ]; then BATCH=12; elif [ "$MB" -ge 11000 ]; then BATCH=8; else BATCH=4; fi
fi

say() { printf '\n== %s\n' "$*"; }
run() { printf '+ %s\n' "$*"; if [ "$DRY" = 0 ]; then "$@"; fi; }
runenv() { # runenv "VAR=val ..." cmd...
  local e="$1"; shift
  printf '+ env %s %s\n' "$e" "$*"
  if [ "$DRY" = 0 ]; then env $e "$@"; fi
}

[ "$DRY" = 1 ] && echo "(dry run: nothing is executed or created)"
echo "voice: $VOICE   gpu: $GPU   batch: $BATCH   s1 epochs: $S1_EPOCHS   s2 epochs: $S2_EPOCHS"

if [ "$DRY" = 0 ]; then
  for f in "$LIST" "$S2G" "$S2D" "$S1" "$BERT" "$HUBERT"; do
    [ -e "$f" ] || { echo "missing: $f (see README, pretrained models)" >&2; exit 1; }
  done
fi

say "0. work dirs"
run mkdir -p "$EXP" "$OUT/weights/gpt" "$OUT/weights/sovits" "$OUT/audition"

cd_g() { if [ "$DRY" = 0 ]; then cd "$G"; fi; echo "+ cd $G"; }
cd_g
COMMON="inp_text=$LIST inp_wav_dir=$WAVS exp_name=$VOICE opt_dir=$EXP i_part=0 all_parts=1 _CUDA_VISIBLE_DEVICES=$GPU is_half=True"

say "1a. text and BERT features (formatting)"
runenv "$COMMON bert_pretrained_dir=$BERT" python GPT_SoVITS/prepare_datasets/1-get-text.py
if [ "$DRY" = 0 ]; then cp "$EXP/2-name2text-0.txt" "$EXP/2-name2text.txt"; else echo "+ cp $EXP/2-name2text-0.txt $EXP/2-name2text.txt"; fi

say "1b. hubert features and 32 kHz wavs"
runenv "$COMMON cnhubert_base_dir=$HUBERT" python GPT_SoVITS/prepare_datasets/2-get-hubert-wav32k.py

say "1c. semantic tokens"
runenv "$COMMON pretrained_s2G=$S2G s2config_path=GPT_SoVITS/configs/s2.json" python GPT_SoVITS/prepare_datasets/3-get-semantic.py
if [ "$DRY" = 0 ]; then
  { printf 'item_name\tsemantic_audio\n'; cat "$EXP/6-name2semantic-0.tsv"; } > "$EXP/6-name2semantic.tsv"
else echo "+ merge $EXP/6-name2semantic-0.tsv into $EXP/6-name2semantic.tsv"; fi

say "2. write the s1 and s2 configs from upstream's templates"
S1CFG="$EXP/s1.yaml"; S2CFG="$EXP/s2.json"
if [ "$DRY" = 0 ]; then
  python - "$G" "$EXP" "$OUT" "$VOICE" "$BATCH" "$S1_EPOCHS" "$S2_EPOCHS" "$S1" "$S2G" "$S2D" <<'PY'
import json, sys, yaml
g, exp, out, voice, batch, e1, e2, s1, s2g, s2d = sys.argv[1:]
batch, e1, e2 = int(batch), int(e1), int(e2)
c = json.load(open(f"{g}/GPT_SoVITS/configs/s2.json"))
c["train"].update(batch_size=batch, epochs=e2, save_every_epoch=max(1, e2 // 4), pretrained_s2G=s2g, pretrained_s2D=s2d,
                  if_save_latest=True, if_save_every_weights=True, save_weight_dir=f"{out}/weights/sovits", gpu_numbers="0")
c["data"]["exp_dir"] = exp
c["model"]["version"] = "v2"
c["name"] = voice
json.dump(c, open(f"{exp}/s2.json", "w"), indent=2)
y = yaml.safe_load(open(f"{g}/GPT_SoVITS/configs/s1longer-v2.yaml"))
y["train"].update(batch_size=batch, epochs=e1, save_every_n_epoch=max(1, e1 // 3), if_save_latest=True,
                  if_save_every_weights=True, half_weights_save_dir=f"{out}/weights/gpt", exp_name=voice)
y["pretrained_s1"] = s1
y["train_semantic_path"] = f"{exp}/6-name2semantic.tsv"
y["train_phoneme_path"] = f"{exp}/2-name2text.txt"
y["output_dir"] = f"{exp}/logs_s1"
yaml.safe_dump(y, open(f"{exp}/s1.yaml", "w"))
PY
else echo "+ python (edit s2.json and s1longer-v2.yaml templates) -> $S2CFG, $S1CFG"; fi

say "3. s2 training (SoVITS, the voice's sound)"
runenv "_CUDA_VISIBLE_DEVICES=$GPU" python GPT_SoVITS/s2_train.py --config "$S2CFG"

say "4. s1 training (GPT, the voice's timing and manner)"
runenv "_CUDA_VISIBLE_DEVICES=$GPU hz=25hz" python GPT_SoVITS/s1_train.py --config_file "$S1CFG"

say "5. record the weights"
if [ "$DRY" = 0 ]; then
  GPT=$(ls -t "$OUT"/weights/gpt/*.ckpt | head -1); SOV=$(ls -t "$OUT"/weights/sovits/*.pth | head -1)
  echo "gpt: $GPT"; echo "sovits: $SOV"
  echo "Now pick a 3 to 10 s reference clip and its exact text, then write $OUT/voice.json (README, serve)."
else
  echo "+ list newest weights in $OUT/weights/{gpt,sovits}"
fi
echo; echo "done: $OUT"
