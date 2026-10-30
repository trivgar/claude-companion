package app.companion;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Build;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.Map;

// CompanionApp's notifications, drawn here (2026-10-04, a person: "Move them into our
// app yeah"). The server sends a data message {kind: notify, title, body, url,
// tag, image?, actions?} (app/commsapp.py notify) whenever it would have sent a
// Web Push to him. The same tag replaces the last one, as on the web; a tap
// opens its page in the app; a button opens it with act=<action>, as sw.js does.
final class Notifier {
    // One channel per kind (2026-10-22, a person: "Can we do different notification
    // categories?"), so each has its own sound and switch in Android's settings.
    // The server names it (app/commsapp.py channel_for, same ids); an id not here
    // goes in the default channel. All start loud, as the one channel was: he quiets what he
    // wants, and Android keeps his choice (the app cannot change it after).
    static final String[][] CHANNELS = {
            {"clips", "Agent clips", "What an agent just said on comms"},
            {"needs", "Agents need you", "A question or a permission waiting on you"},
            {"stuck", "Stuck asks", "A question or a permission waiting on you 30 minutes"},
            {"deploys", "Deploys", "A deploy that went out or failed"},
            {"apps", "App alerts", "Alerts from your own apps: trials, reviews, errors"},
            {"usage", "Usage", "Budget lines and sessions that need a /clear"},
            {"comms", "Comms", "Comms held, paused or resumed"},
            {"todo", "To-do", "Someone added to your list"},
            {"money", "Money", "Money and trading"},
            {"hardware", "Hardware", "Machines, drives and the printer"},
            {"scrapers", "Watchers", "Scrapers and watchers that broke or found something"},
    };

    private Notifier() {}

    static String channel(Context c) {
        return c.getString(R.string.app_slug);
    }

    static String extraUrl(Context c) {
        return channel(c) + ".url";
    }

    static void show(Context c, Map<String, String> wire) {
        // Google carried only an id (and the tag); the words come from
        // CompanionApp over the tailnet, as a clip does (/api/tts/note/{id}).
        Map<String, String> d = new java.util.HashMap<>(wire);
        String id = wire.get("id");
        if (id != null && id.matches("[A-Za-z0-9_-]{1,40}")) {
            // Twice: on cellular the first try can die while the tunnel wakes
            // (2026-10-04 18:02, the muted-phone notice read "Something new").
            for (int attempt = 0; attempt < 2 && !d.containsKey("title"); attempt++) {
                try {
                    if (attempt > 0) Thread.sleep(1500);
                    byte[] b = CompanionApp.get(c, "/api/tts/note/" + id, 6000);
                    if (b != null && b.length > 0) {
                        JSONObject o = new JSONObject(new String(b, "UTF-8"));
                        for (java.util.Iterator<String> it = o.keys(); it.hasNext(); ) {
                            String k = it.next();
                            d.put(k, o.optString(k, ""));
                        }
                    }
                    if (b != null && b.length == 0) break;
                } catch (Exception ignored) {
                    // CompanionApp out of reach: still say what kind of thing happened
                }
            }
        }
        if (!d.containsKey("title")) {
            // Only what came through Google: one word for its kind, and its page.
            String label = wire.get("label");
            d.put("title", label == null || label.isEmpty() ? c.getString(R.string.app_name) : label);
            d.put("body", "Could not fetch the words. Tap to open.");
        }
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        String channel = channel(c, nm, wire.get("channel"));
        String title = val(d, "title", c.getString(R.string.app_name));
        String body = val(d, "body", "");
        String url = val(d, "url", "/");
        String tag = val(d, "tag", channel(c));
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(c, channel) : new Notification.Builder(c);
        b.setSmallIcon(R.drawable.ic_stat_comms)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .setContentIntent(open(c, url, tag.hashCode()));
        String image = d.get("image");
        if (image != null && image.startsWith("/")) {
            try {
                byte[] bytes = CompanionApp.get(c, image, 6000);
                Bitmap bm = bytes == null ? null : BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
                if (bm != null) b.setStyle(new Notification.BigPictureStyle().bigPicture(bm).setSummaryText(body));
            } catch (Exception ignored) {
                // the words still go
            }
        }
        try {
            JSONArray acts = new JSONArray(val(d, "actions", "[]"));
            for (int i = 0; i < acts.length() && i < 3; i++) {
                JSONObject a = acts.getJSONObject(i);
                String act = a.optString("action", "");
                if (!act.matches("[a-z]{1,20}")) continue;
                String to = url + (url.contains("?") ? "&" : "?") + "act=" + act;
                b.addAction(new Notification.Action.Builder(null, a.optString("title", act), open(c, to, (tag + act).hashCode())).build());
            }
        } catch (Exception ignored) {
        }
        nm.notify(tag, 0, b.build());
    }

    // The channel for this id, made the first time it is used. Creating one that
    // exists only refreshes its name and description, never his settings.
    private static String channel(Context c, NotificationManager nm, String want) {
        String[] row = null;
        for (String[] r : CHANNELS) if (r[0].equals(want)) row = r;
        String id = row == null ? channel(c) : row[0];
        String name = row == null ? "Everything else" : row[1];
        String description = row == null ? "Anything without its own channel" : row[2];
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(id, name, NotificationManager.IMPORTANCE_HIGH);
            ch.setDescription(description);
            nm.createNotificationChannel(ch);
        }
        return id;
    }

    private static PendingIntent open(Context c, String url, int code) {
        Intent i = new Intent(c, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(extraUrl(c), url);
        return PendingIntent.getActivity(c, code, i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static String val(Map<String, String> d, String k, String dflt) {
        String v = d.get(k);
        return v == null || v.isEmpty() ? dflt : v;
    }
}
