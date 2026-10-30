package app.companion;

import android.net.Uri;
import android.webkit.PermissionRequest;

import com.getcapacitor.Bridge;
import com.getcapacitor.BridgeWebChromeClient;

// The live call's microphone (static/call.js): the page asks through
// getUserMedia and only CompanionApp's own page gets it. Capacitor's client grants
// any page that asks once Android allows RECORD_AUDIO; this WebView only loads
// CompanionApp, but a link could take it elsewhere, so any other origin, and
// anything but the microphone, is refused here before Capacitor asks Android.
final class CallChromeClient extends BridgeWebChromeClient {
    private final String origin;

    CallChromeClient(Bridge bridge, String origin) {
        super(bridge);
        this.origin = origin;
    }

    @Override
    public void onPermissionRequest(final PermissionRequest request) {
        Uri asked = request.getOrigin();
        String from = asked == null ? "" : asked.getScheme() + "://" + asked.getAuthority();
        if (!from.equalsIgnoreCase(origin)) {
            request.deny();
            return;
        }
        for (String r : request.getResources()) {
            if (!PermissionRequest.RESOURCE_AUDIO_CAPTURE.equals(r)) {
                request.deny();
                return;
            }
        }
        super.onPermissionRequest(request);
    }
}
