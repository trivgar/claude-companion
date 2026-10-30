# Voice lab: make a voice on an Nvidia card

This is the whole path for a Claude to follow with its owner: clips in, a
voice out that comms can speak with. It uses GPT-SoVITS v2
(https://github.com/RVC-Boss/GPT-SoVITS) on CUDA, inside WSL2.

**Consent.** The clean case is your own voice, or people who have agreed to
it, in writing if it matters to you. Do not clone someone who has not
agreed, and do not use clips you have no right to. Everything here stays on
your machine; nothing is uploaded.

Files here: `prep.py` (clips to dataset), `train.sh` (training), `serve.py`
(HTTP voice server). The dashboard's Voice lab tab shows what exists, read
only.

## 1. Install

Needs a Windows PC with an Nvidia card (12 GB works, 16 to 24 GB is comfortable).

1. **Driver.** Install the normal Windows Nvidia driver. Do not install a
   Linux driver inside WSL2: it uses the Windows one. Check in WSL with
   `nvidia-smi`; it must list the card. If not, update the Windows driver
   and run `wsl --update` from PowerShell.
2. **Tools in WSL:** `sudo apt install -y ffmpeg git build-essential`.
3. **Python env** (conda is what upstream tests; a venv on Python 3.10 also works):
   ```
   conda create -n gptsovits python=3.10 -y && conda activate gptsovits
   ```
4. **Clone and install:**
   ```
   git clone https://github.com/RVC-Boss/GPT-SoVITS ~/GPT-SoVITS
   cd ~/GPT-SoVITS
   pip install -r extra-req.txt --no-deps   # if the repo has it
   pip install -r requirements.txt
   ```
   Follow upstream's README for the CUDA build of PyTorch it names; if
   `python -c "import torch; print(torch.cuda.is_available())"` prints False,
   fix that before going on.
5. **Pretrained models.** Download them as upstream's README says (the
   `GPT_SoVITS/pretrained_models` folder from their Hugging Face repo
   `lj1995/GPT-SoVITS`). `train.sh` expects:
   - `pretrained_models/gsv-v2final-pretrained/` with `s2G2333k.pth`,
     `s2D2333k.pth` and the `s1bert25hz-...ckpt`
   - the BERT folder, `pretrained_models/chinese-r*-wwm-ext-large/`
   - `pretrained_models/chinese-hubert-base/`

   File names move between upstream releases. If `train.sh --dry-run` shows a
   path that does not exist, edit the names at the top of `train.sh`.
6. Optional, for automatic transcripts: `pip install faster-whisper`
   (in the same env). Without it `prep.py` writes empty text.

Then: `export GPT_SOVITS_DIR=~/GPT-SoVITS`.

## 2. Gather clips

Ten minutes of clean speech is the floor; thirty to sixty is better. One
speaker, little music or noise, no overlapping voices. Put the files (wav,
mp3, m4a, flac and so on) in one folder, for example `~/clips/myvoice`.
Subfolders are fine.

## 3. Prep

```
python3 prep.py ~/clips/myvoice ~/datasets/myvoice myvoice --lang en
```

Produces `~/datasets/myvoice/wavs/*.wav` (mono, 32 kHz, 3 to 12 s, cut on
silence) and `myvoice.list` in GPT-SoVITS's `path|speaker|LANG|text` format.
Open the list and fix wrong transcripts: bad text trains a bad voice. Delete
lines for pieces with music, laughter or a second speaker. `--lang` is `en`,
`zh`, `ja`, `ko` or `yue`.

## 4. Train

```
./train.sh --dry-run myvoice ~/datasets/myvoice   # read the commands first
./train.sh myvoice ~/datasets/myvoice
```

Stages are printed as they run: text/BERT features, hubert features,
semantic tokens, then s2 (the sound) and s1 (timing and manner) training.
Batch size is picked from the card's VRAM (4 under 11 GB, 8 to 22 GB, 12
above); override with `BATCH=`, `S1_EPOCHS=` (default 15), `S2_EPOCHS=` (default 8).
If it runs out of memory, lower `BATCH`. Training hogs the GPU; do not game
meanwhile. Output goes to
`~/.local/share/claude-companion/voices/myvoice/` (`weights/gpt/*.ckpt`,
`weights/sovits/*.pth`).

## 5. Audition and register

1. Choose a clean 3 to 10 s reference clip from the dataset and copy it to
   `voices/myvoice/ref.wav`.
2. Write `voices/myvoice/voice.json` (paths relative to that folder):
   ```json
   {"gpt": "weights/gpt/<newest>.ckpt", "sovits": "weights/sovits/<newest>.pth",
    "ref_audio": "ref.wav", "ref_text": "exactly what the reference says", "lang": "en"}
   ```
3. Serve (step 6) and render a few test lines. Try several epochs'
   weights (`s1` and `s2` save at intervals) and keep the best; later is not
   always better. Save the keepers as `voices/myvoice/audition/*.wav`: the
   dashboard shows them with players.

## 6. Serve

```
python3 serve.py --list          # voices found
python3 serve.py                 # http://127.0.0.1:9880
curl -s -X POST localhost:9880/tts -H 'Content-Type: application/json' \
  -H "X-Voice-Token: $(cat ~/.config/claude-companion/voice-token)" \
  -d '{"voice":"myvoice","text":"Testing one two."}' -o test.wav
```

`GET /voices` lists names (it needs the same header). The first request loads
the model (slow); the next are fast. It binds to loopback. Every request must
carry `X-Voice-Token`, the contents of `~/.config/claude-companion/voice-token`
(mode 600, made on first run).

## 7. Hook into comms

Comms already supports this. In `comms/cast.tsv`, write a speaker's voice as
`server:<name>` (for example `Juno<TAB>server:myvoice`) and `comms/speak.sh`
POSTs the text to this server with the token above, then plays the wav. If the
server is down or refuses, it falls back to the first Piper row in the file,
so keep at least one Piper row. `COMMS_VOICE_PORT` changes the port (default
9880). Run `serve.py` in the same WSL instance as the comms script, at login,
with a systemd user unit or your shell profile.
