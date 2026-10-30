package app.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Person;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.os.Build;

import org.json.JSONObject;

// The ring itself: a call notification with a full-screen screen, the phone's
// ringtone, ringing until answered, declined or missed.
final class RingNotice {
    static final String CHANNEL = "ring";
    static final String TAG = "cc-ring";
    static final int ID = 7;

    private RingNotice() {}

    static void show(Context c, JSONObject b) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Calls from Caller", NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription("An agent stuck 30 minutes on a question for you");
            ch.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE), new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            ch.enableVibration(true);
            nm.createNotificationChannel(ch);
        }
        int imm = PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE;
        PendingIntent full = PendingIntent.getActivity(c, 70, new Intent(c, RingActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_USER_ACTION), imm);
        PendingIntent answer = PendingIntent.getActivity(c, 71, new Intent(c, RingActivity.class)
                .setAction(RingActivity.answer(c)).setFlags(Intent.FLAG_ACTIVITY_NEW_TASK), imm);
        PendingIntent decline = PendingIntent.getBroadcast(c, 72, new Intent(c, RingActions.class)
                .setAction(RingActions.decline(c)), imm);
        String who = b.optString("who", "an agent");
        Notification.Builder nb = (Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, CHANNEL) : new Notification.Builder(c))
                .setSmallIcon(R.drawable.ic_stat_comms)
                .setContentTitle("Caller, for " + who)
                .setContentText(b.optString("title", ""))
                .setCategory(Notification.CATEGORY_CALL)
                .setFullScreenIntent(full, true)
                .setOngoing(true);
        if (Build.VERSION.SDK_INT >= 31) {
            Person caller = new Person.Builder().setName("Caller").setImportant(true).build();
            nb.setStyle(Notification.CallStyle.forIncomingCall(caller, decline, answer));
        } else {
            nb.addAction(new Notification.Action.Builder(null, "Decline", decline).build());
            nb.addAction(new Notification.Action.Builder(null, "Answer", answer).build());
        }
        Notification n = nb.build();
        n.flags |= Notification.FLAG_INSISTENT;
        nm.notify(TAG, ID, n);
    }

    static void cancel(Context c) {
        c.getSystemService(NotificationManager.class).cancel(TAG, ID);
    }
}
