package app.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioDeviceInfo;
import android.media.AudioFocusRequest;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

// The comms player, started by a push. It does what Termux's tts-fetch.sh
// does, once per wake instead of forever: ask the outbox as who=app, play
// every clip in its order (breaking news first), answer played, skipped or
// failed, ask again, and stop when nothing is left. Android wants a
// foreground service of type mediaPlayback to play with the screen off.
//
// Spotify is ducked under a clip, not paused (a person, 2026-10-03, picking
// "Duck it"): one transient may-duck focus held for the whole batch, so the
// music dips once and comes back once.
public class CommsPlayerService extends Service {
    static final String CHANNEL = "comms";
    static final int STALE_S = 300;          // tts-fetch.sh's rule: older is news no more
    static final long LEAD_MS = 350;         // a car's Bluetooth swallows the first of a woken stream
    static final long TAIL_MS = 1200;        // and plays behind: answer once it has left the speakers

    private static final AtomicBoolean running = new AtomicBoolean(false);
    private static final AtomicBoolean again = new AtomicBoolean(false);

    // The batch's audio focus, held once for the whole run and only for real
    // clips. A probe (a device check, spec 2026-10-12-device-verify-design.md)
    // never asks for focus at all, so Spotify is untouched.
    private AudioManager am;
    private AudioFocusRequest focusReq;
    private boolean focused = false;

    static void wake(Context c) {
        again.set(true);
        Intent i = new Intent(c, CommsPlayerService.class);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
        else c.startService(i);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = notification("Comms");
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(1, n);
        }
        if (running.compareAndSet(false, true)) {
            new Thread(this::run, "cc-comms").start();
        }
        return START_NOT_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    private Notification notification(String text) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Comms playing", NotificationManager.IMPORTANCE_LOW);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(R.drawable.ic_stat_comms)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(text)
                .setOngoing(true)
                .build();
    }

    private void run() {
        PowerManager.WakeLock wl = ((PowerManager) getSystemService(POWER_SERVICE))
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, getString(R.string.app_slug) + ":comms");
        wl.acquire(10 * 60 * 1000L);
        am = (AudioManager) getSystemService(AUDIO_SERVICE);
        // MEDIA, not ASSISTANT: on 2026-10-06 07:33-07:36, in a car over
        // Bluetooth, seven clips as ASSISTANT ducked the music for their full
        // length and were never heard (the assistant stream has its own route
        // and volume, which the car did not carry). Media is the stream the
        // music is on, at the volume you set.
        AudioAttributes attrs = new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build();
        focusReq = Build.VERSION.SDK_INT >= 26
                ? new AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
                    .setAudioAttributes(attrs).setOnAudioFocusChangeListener(f -> {}).build()
                : null;
        focused = false;
        Set<Integer> handled = new HashSet<>();
        try {
            // Asked until empty: a push that lands mid-batch only sets
            // `again`, and the next ask finds its clip.
            while (true) {
                again.set(false);
                JSONArray clips = ask();
                int played = 0;
                for (int i = 0; clips != null && i < clips.length(); i++) {
                    JSONObject c = clips.getJSONObject(i);
                    int seq = c.getInt("seq");
                    if (!handled.add(seq)) continue;
                    double age = c.optDouble("age", 0);
                    if (age > STALE_S) {
                        answer(seq, "skipped", "stale " + Math.round(age) + "s");
                        continue;
                    }
                    byte[] audio = fetch(seq);
                    if (audio == null) {
                        answer(seq, "skipped", "gone");
                        continue;
                    }
                    if (audio.length == 0) {          // the network, not the clip: leave it unanswered
                        handled.remove(seq);
                        continue;
                    }
                    boolean probe = c.optBoolean("probe", false);
                    if (!probe && ensureFocus()) {
                        Thread.sleep(LEAD_MS);
                    }
                    // A clip and every one taken ahead behind it.
                    Clip cur = prepare(seq, c.optString("name", ""), audio, attrs, probe);
                    while (cur != null) {
                        if (!cur.probe) notifyName(cur.name);   // a probe never posts "Comms playing"
                        if (cur.mp != null) {
                            cur.mp.start();
                            if (!cur.chained && cur.t0 == 0) cur.t0 = System.currentTimeMillis();
                        }
                        Clip nxt = lookAhead(cur, handled, attrs);
                        String err = finish(cur);
                        if (nxt != null) startIfNotChained(nxt);
                        if (err == null) {
                            Thread.sleep(TAIL_MS);
                            answer(cur.seq, "played", playedDetail(cur));
                            played++;
                        } else {
                            answer(cur.seq, "failed", err);
                        }
                        cur = nxt;
                        if (cur != null) cur.began = true;
                    }
                }
                if (played == 0 && !again.get()) break;
            }
        } catch (Exception e) {
            Log.w(CompanionApp.TAG, "comms: " + e);
        } finally {
            if (focused) {
                if (Build.VERSION.SDK_INT >= 26) am.abandonAudioFocusRequest(focusReq);
                else am.abandonAudioFocus(f -> {});
            }
            if (wl.isHeld()) wl.release();
            running.set(false);
            // A push that arrived as this was stopping starts a fresh run,
            // asked while this is still in the foreground, where Android allows it.
            if (again.get()) {
                wake(this);
            } else {
                stopForeground(true);
                stopSelf();
            }
        }
    }

    // Asks for the batch's focus once. True only when this call made the request.
    private boolean ensureFocus() {
        if (focused) return false;
        focused = Build.VERSION.SDK_INT >= 26
                ? am.requestAudioFocus(focusReq) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED
                : am.requestAudioFocus(f -> {}, AudioManager.STREAM_MUSIC,
                        AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK) == AudioManager.AUDIOFOCUS_REQUEST_GRANTED;
        return true;
    }

    // What the phone says about a clip that played, at most 200 characters:
    // `v=1.640 t0=<ms epoch> t1=<ms epoch> ch=1 dur=<ms> route=bt`. Facts, not
    // a claim of hearing: MediaPlayer cannot tell volume or a muted headset.
    private String playedDetail(Clip c) {
        long t1 = c.t1 > 0 ? c.t1 : System.currentTimeMillis();
        long t0 = c.t0 > 0 ? c.t0 : t1;
        String d = "v=" + BuildConfig.VERSION_NAME + " t0=" + t0 + " t1=" + t1 + " ch=" + (c.chained ? 1 : 0)
                + " dur=" + (t1 - t0) + " route=" + route();
        return d.length() > 200 ? d.substring(0, 200) : d;
    }

    private String route() {
        try {
            boolean bt = false, wired = false, speaker = false;
            for (AudioDeviceInfo d : am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
                switch (d.getType()) {
                    case AudioDeviceInfo.TYPE_BLUETOOTH_A2DP:
                    case AudioDeviceInfo.TYPE_BLUETOOTH_SCO:
                        bt = true; break;
                    case AudioDeviceInfo.TYPE_WIRED_HEADSET:
                    case AudioDeviceInfo.TYPE_WIRED_HEADPHONES:
                    case AudioDeviceInfo.TYPE_USB_HEADSET:
                    case AudioDeviceInfo.TYPE_USB_DEVICE:
                        wired = true; break;
                    case AudioDeviceInfo.TYPE_BUILTIN_SPEAKER:
                        speaker = true; break;
                    default:
                        if (Build.VERSION.SDK_INT >= 31 && (d.getType() == AudioDeviceInfo.TYPE_BLE_HEADSET
                                || d.getType() == AudioDeviceInfo.TYPE_BLE_SPEAKER)) bt = true;
                }
            }
            return bt ? "bt" : wired ? "wired" : speaker ? "speaker" : "other";
        } catch (Exception e) {
            return "other";
        }
    }

    private JSONArray ask() {
        for (int i = 0; i < 3; i++) {
            try {
                byte[] b = CompanionApp.get(this, "/api/tts/outbox?who=app&after=0&wait=0", 15000);
                if (b != null) return new JSONObject(new String(b, "UTF-8")).optJSONArray("clips");
            } catch (Exception e) {
                Log.w(CompanionApp.TAG, "ask: " + e);
            }
            sleep(1500L * (i + 1));
        }
        return null;
    }

    // One try, for the look-ahead: a failure waits for the next look.
    private JSONArray askOnce() {
        try {
            byte[] b = CompanionApp.get(this, "/api/tts/outbox?who=app&after=0&wait=0", 8000);
            if (b != null) return new JSONObject(new String(b, "UTF-8")).optJSONArray("clips");
        } catch (Exception e) {
            Log.w(CompanionApp.TAG, "ask ahead: " + e);
        }
        return null;
    }

    // null: gone (404). Empty: the network failed once.
    private byte[] fetchOnce(int seq) {
        try {
            return CompanionApp.get(this, "/api/tts/clip/" + seq + "?who=app", 30000);
        } catch (Exception e) {
            Log.w(CompanionApp.TAG, "fetch ahead #" + seq + ": " + e);
        }
        return new byte[0];
    }

    // null: the clip is gone (404). Empty: the network failed three times.
    private byte[] fetch(int seq) {
        for (int i = 0; i < 3; i++) {
            try {
                return CompanionApp.get(this, "/api/tts/clip/" + seq + "?who=app", 30000);
            } catch (Exception e) {
                Log.w(CompanionApp.TAG, "fetch #" + seq + ": " + e);
            }
            sleep(1500L * (i + 1));
        }
        return new byte[0];
    }

    private void answer(int seq, String result, String detail) {
        for (int i = 0; i < 3; i++) {
            try {
                JSONObject b = new JSONObject().put("seq", seq).put("result", result).put("detail", detail);
                int code = CompanionApp.post(this, "/api/tts/played", b.toString(), false);
                if (code == 200 || code == 404) return;
            } catch (Exception e) {
                Log.w(CompanionApp.TAG, "answer #" + seq + ": " + e);
            }
            sleep(2000L * (i + 1));
        }
    }

    // One clip, prepared (decoded headers, file written) and maybe waiting to
    // be started, or chained behind the playing one.
    private static final class Clip {
        int seq;
        String name = "";
        MediaPlayer mp;
        File f;
        String err;                        // set when it could not be prepared or played
        long ms;                           // how long to wait for its end
        long since;                        // when it began playing (or was promoted)
        boolean probe;                     // a device check: silent, no focus
        volatile long t0, t1;              // wall clock, ms: started (or chained) and completed
        boolean began;                     // playing, not merely prepared
        volatile boolean chained;          // started by setNextMediaPlayer
        final CountDownLatch done = new CountDownLatch(1);
        final CountDownLatch startedAsNext = new CountDownLatch(1);
    }

    private void notifyName(String name) {
        getSystemService(NotificationManager.class).notify(1, notification(name.isEmpty() ? "Comms" : name));
    }

    // Fetched ahead (2026-10-12, gapless-shows spec Part B): the next clip is
    // prepared while this one plays and chained with setNextMediaPlayer, so
    // the player moves to it with no ask, download or prepare in between.
    private Clip prepare(int seq, String name, byte[] audio, AudioAttributes attrs, boolean probe) {
        Clip c = new Clip();
        c.probe = probe;
        c.seq = seq;
        c.name = name;
        c.f = new File(getCacheDir(), "clip-" + seq + ".opus");
        MediaPlayer mp = new MediaPlayer();
        try (FileOutputStream o = new FileOutputStream(c.f)) {
            o.write(audio);
        } catch (Exception e) {
            mp.release();
            c.err = "write: " + e.getMessage();
            c.done.countDown();
            return c;
        }
        try {
            mp.setAudioAttributes(attrs);
            mp.setWakeMode(this, PowerManager.PARTIAL_WAKE_LOCK);
            mp.setDataSource(c.f.getAbsolutePath());
            if (probe) mp.setVolume(0f, 0f);
            mp.setOnCompletionListener(m -> { c.t1 = System.currentTimeMillis(); c.done.countDown(); });
            mp.setOnErrorListener((m, what, extra) -> {
                c.err = "player " + what + "/" + extra;
                c.done.countDown();
                return true;
            });
            mp.setOnInfoListener((m, what, extra) -> {
                if (what == MediaPlayer.MEDIA_INFO_STARTED_AS_NEXT) {
                    c.chained = true;
                    c.t0 = System.currentTimeMillis();
                    c.startedAsNext.countDown();
                }
                return false;
            });
            mp.prepare();
            c.ms = Math.max(1000, mp.getDuration()) + 15000;
            c.mp = mp;
        } catch (Exception e) {
            mp.release();
            c.err = "player: " + e.getMessage();
            c.done.countDown();
        }
        c.since = System.currentTimeMillis();
        return c;
    }

    // While `cur` plays: ask for the next waiting clip, take and prepare it,
    // and chain it behind. Returns it (or null) when cur has ended. A clip
    // that cannot be had is answered or left, as the main loop would.
    private Clip lookAhead(Clip cur, Set<Integer> handled, AudioAttributes attrs) throws InterruptedException {
        Clip nxt = null;
        cur.since = System.currentTimeMillis();
        while (!cur.done.await(nxt == null ? 1500 : 500, TimeUnit.MILLISECONDS)) {
            if (System.currentTimeMillis() - cur.since > cur.ms) {
                if (cur.err == null) cur.err = "player: no end";
                break;
            }
            if (nxt != null) continue;
            JSONArray clips = askOnce();
            for (int i = 0; clips != null && i < clips.length() && nxt == null; i++) {
                JSONObject c = clips.optJSONObject(i);
                if (c == null) continue;
                int seq = c.optInt("seq", -1);
                if (seq < 0 || !handled.add(seq)) continue;
                double age = c.optDouble("age", 0);
                if (age > STALE_S) {
                    answer(seq, "skipped", "stale " + Math.round(age) + "s");
                    continue;
                }
                byte[] audio = fetchOnce(seq);
                if (audio == null) {                  // 404, or another player has it
                    answer(seq, "skipped", "gone");
                    continue;
                }
                if (audio.length == 0) {              // the network: leave it for the main loop
                    handled.remove(seq);
                    break;
                }
                boolean probe = c.optBoolean("probe", false);
                Clip n = prepare(seq, c.optString("name", ""), audio, attrs, probe);
                if (n.mp == null) {
                    answer(seq, "failed", n.err == null ? "prepare" : n.err);
                    cleanup(n);
                    continue;
                }
                if (!probe) ensureFocus();            // a real clip chained behind a probe still ducks the music
                try {
                    cur.mp.setNextMediaPlayer(n.mp);
                } catch (Exception e) {
                    // cur already ended: it is started by hand when cur is finished
                    Log.w(CompanionApp.TAG, "chain #" + seq + ": " + e);
                }
                nxt = n;
            }
        }
        return nxt;
    }

    // If the chain did not carry it (cur failed, or ended before the link),
    // start it now.
    private void startIfNotChained(Clip n) throws InterruptedException {
        if (n.mp == null || n.began) return;
        if (!n.startedAsNext.await(600, TimeUnit.MILLISECONDS)) {
            try { n.mp.start(); n.t0 = System.currentTimeMillis(); } catch (Exception e) { n.err = "start: " + e.getMessage(); n.done.countDown(); }
        }
        n.since = System.currentTimeMillis();
    }

    // Waits out the clip (lookAhead already did unless it was never started)
    // and releases it. null when it played, else the reason.
    private String finish(Clip c) throws InterruptedException {
        if (c.mp != null && !c.done.await(c.ms, TimeUnit.MILLISECONDS) && c.err == null) c.err = "player: no end";
        cleanup(c);
        String e = c.err;
        return e == null ? null : e.substring(0, Math.min(80, e.length()));
    }

    private static void cleanup(Clip c) {
        if (c.mp != null) {
            try { c.mp.release(); } catch (Exception ignored) { }
            c.mp = null;
        }
        if (c.f != null) c.f.delete();
    }

    private static void sleep(long ms) {
        try {
            Thread.sleep(ms);
        } catch (InterruptedException ignored) {
        }
    }
}
