package app.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import org.json.JSONObject;

// A ring's brief, fetched in the foreground: inside Google's own background
// wake-up a fetch on mobile data never left the phone (NoteService, 2026-10-05).
public class RingService extends Service {
    private static final int ID = 3;

    static void fetch(Context c, String rid) {
        Intent i = new Intent(c, RingService.class).putExtra("ring", rid);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
        else c.startService(i);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = holding();
        if (Build.VERSION.SDK_INT >= 34) startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE);
        else startForeground(ID, n);
        final String rid = intent == null ? null : intent.getStringExtra("ring");
        new Thread(() -> {
            try {
                if (rid != null && rid.matches("[0-9a-f]{16}")) {
                    byte[] b = null;
                    for (int attempt = 0; attempt < 2 && (b == null || b.length == 0); attempt++) {
                        if (attempt > 0) Thread.sleep(1500);
                        try { b = CompanionApp.get(this, "/api/ring/" + rid, 6000); } catch (Exception ignored) {}
                    }
                    if (b != null && b.length > 0) {
                        JSONObject brief = new JSONObject(new String(b, "UTF-8"));
                        new android.os.Handler(getMainLooper()).post(() -> Ringer.incoming(getApplicationContext(), brief));
                    }
                }
            } catch (Exception e) {
                Log.w(CompanionApp.TAG, "ring fetch: " + e);
            } finally {
                stopSelf();
            }
        }, "cc-ring").start();
        return START_NOT_STICKY;
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        stopSelf();
    }

    private Notification holding() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(NoteService.CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(NoteService.CHANNEL, "Comms playing", NotificationManager.IMPORTANCE_LOW);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, NoteService.CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(R.drawable.ic_stat_comms).setContentTitle(getString(R.string.app_name)).setContentText("Caller is calling").build();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
