package app.companion;

import android.content.Context;
import android.net.Uri;
import android.util.Log;

import com.getcapacitor.CapConfig;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

// Connects only to the configured server address.
final class CompanionApp {
    static final String TAG = "Companion";

    private CompanionApp() {}

    static Uri server(Context c) {
        return Uri.parse(CapConfig.loadDefault(c).getServerUrl());
    }

    static String base(Context c) {
        Uri u = server(c);
        return u.getScheme() + "://" + u.getAuthority();
    }

    static void register(Context c, String token) {
        final Context app = c.getApplicationContext();
        new Thread(() -> {
            try {
                // notify: this build draws CompanionApp's notifications (Notifier).
                JSONObject body = new JSONObject().put("token", token).put("notify", true).put("ring", true);
                int code = post(app, "/api/tts/app", body.toString(), true);
                Log.i(TAG, "registered for comms: " + code);
            } catch (Exception e) {
                Log.w(TAG, "register: " + e);
            }
        }, "cc-register").start();
    }

    static HttpURLConnection open(Context c, String path, int timeoutMs) throws Exception {
        HttpURLConnection h = (HttpURLConnection) new URL(base(c) + path).openConnection();
        h.setConnectTimeout(timeoutMs);
        h.setReadTimeout(timeoutMs);
        return h;
    }

    static int post(Context c, String path, String json, boolean withKey) throws Exception {
        HttpURLConnection h = open(c, path, 10000);
        try {
            h.setRequestMethod("POST");
            h.setDoOutput(true);
            h.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = h.getOutputStream()) {
                o.write(json.getBytes(StandardCharsets.UTF_8));
            }
            return h.getResponseCode();
        } finally {
            h.disconnect();
        }
    }

    static byte[] get(Context c, String path, int timeoutMs) throws Exception {
        HttpURLConnection h = open(c, path, timeoutMs);
        try {
            int code = h.getResponseCode();
            if (code == 404) return null;
            if (code == 409) return new byte[0];      // another player holds it: not ours
            if (code != 200) throw new java.io.IOException("http " + code);
            try (InputStream in = h.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                return out.toByteArray();
            }
        } finally {
            h.disconnect();
        }
    }
}
