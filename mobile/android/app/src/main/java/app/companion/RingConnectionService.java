package app.companion;

import android.net.Uri;
import android.telecom.Connection;
import android.telecom.ConnectionRequest;
import android.telecom.ConnectionService;
import android.telecom.PhoneAccountHandle;
import android.telecom.TelecomManager;

// Telecom's side of a ring: it asks for the Connection that Ringer announced.
public class RingConnectionService extends ConnectionService {
    @Override
    public Connection onCreateIncomingConnection(PhoneAccountHandle h, ConnectionRequest req) {
        RingConnection conn = new RingConnection(getApplicationContext(), Ringer.pending);
        Ringer.pending = null;
        conn.setCallerDisplayName("Caller", TelecomManager.PRESENTATION_ALLOWED);
        conn.setAddress(Uri.fromParts(getString(R.string.app_slug), "caller", null), TelecomManager.PRESENTATION_ALLOWED);
        conn.setRinging();
        return conn;
    }

    @Override
    public void onCreateIncomingConnectionFailed(PhoneAccountHandle h, ConnectionRequest req) {
        Ringer.alerts(getApplicationContext(), Ringer.pending);
        Ringer.pending = null;
    }
}
