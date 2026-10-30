package app.companion;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

// Decline, from the call notification.
public class RingActions extends BroadcastReceiver {
    static String decline(Context c) {
        return c.getPackageName() + ".DECLINE";
    }

    @Override
    public void onReceive(Context c, Intent i) {
        RingConnection call = RingConnection.current;
        if (call != null && decline(c).equals(i.getAction())) call.onReject();
    }
}
