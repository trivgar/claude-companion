package app.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.util.Log;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

// Fetches a notification's words in the foreground. Inside Google's own
// background wake-up the fetch never left the phone on mobile data (2026-10-05
// 06:55, nothing reached CompanionApp), while the comms player, a foreground
// service, fetched clips fine on the same 5G. So the words come the player's
// way: a short service, gone the moment the notification is drawn.
public class NoteService extends Service {
    static final String CHANNEL = "comms";        // the player's quiet channel
    private static final int ID = 2;
    private final AtomicInteger busy = new AtomicInteger();

    static void fetch(Context c, Map<String, String> wire) {
        Bundle b = new Bundle();
        for (Map.Entry<String, String> e : wire.entrySet()) b.putString(e.getKey(), e.getValue());
        Intent i = new Intent(c, NoteService.class).putExtra("wire", b);
        if (Build.VERSION.SDK_INT >= 26) c.startForegroundService(i);
        else c.startService(i);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = holding();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SHORT_SERVICE);
        } else {
            startForeground(ID, n);
        }
        Bundle b = intent == null ? null : intent.getBundleExtra("wire");
        final Map<String, String> wire = new HashMap<>();
        if (b != null) for (String k : b.keySet()) wire.put(k, b.getString(k, ""));
        busy.incrementAndGet();
        new Thread(() -> {
            try {
                Notifier.show(this, wire);
            } catch (Exception e) {
                Log.w(CompanionApp.TAG, "note: " + e);
            } finally {
                if (busy.decrementAndGet() == 0) stopSelf();
            }
        }, "cc-note").start();
        return START_NOT_STICKY;
    }

    @Override
    public void onTimeout(int startId, int fgsType) {
        stopSelf();                                 // a short service's three minutes are up
    }

    private Notification holding() {
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel(CHANNEL) == null) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Comms playing", NotificationManager.IMPORTANCE_LOW);
            ch.setShowBadge(false);
            nm.createNotificationChannel(ch);
        }
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        return b.setSmallIcon(R.drawable.ic_stat_comms)
                .setContentTitle(getString(R.string.app_name))
                .setContentText("Fetching a notification")
                .build();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
