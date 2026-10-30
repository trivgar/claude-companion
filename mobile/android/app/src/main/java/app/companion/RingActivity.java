package app.companion;

import android.content.Context;
import android.app.Activity;
import android.app.KeyguardManager;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

// The full-screen ring, over the lock screen: who and why, Answer and Decline.
// Answer asks Android to unlock first, so the CompanionApp app itself never shows
// over the lock screen.
public class RingActivity extends Activity {
    static String answer(Context c) {
        return c.getPackageName() + ".ANSWER";
    }

    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        RingConnection call = RingConnection.current;
        if (call == null) { finish(); return; }
        if (answer(this).equals(getIntent().getAction())) { answer(call); return; }
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setPadding(48, 48, 48, 48);
        TextView who = new TextView(this);
        who.setText("Caller, for " + call.brief().optString("who", "an agent"));
        who.setTextSize(26);
        TextView why = new TextView(this);
        why.setText(call.brief().optString("title", ""));
        why.setTextSize(18);
        Button yes = new Button(this);
        yes.setText("Answer");
        yes.setMinHeight(144);
        yes.setOnClickListener(v -> answer(call));
        Button no = new Button(this);
        no.setText("Decline");
        no.setMinHeight(144);
        no.setOnClickListener(v -> { call.onReject(); finish(); });
        box.addView(who);
        box.addView(why);
        box.addView(yes);
        box.addView(no);
        setContentView(box);
    }

    private void answer(RingConnection call) {
        KeyguardManager km = getSystemService(KeyguardManager.class);
        if (Build.VERSION.SDK_INT >= 26 && km != null && km.isKeyguardLocked()) {
            km.requestDismissKeyguard(this, new KeyguardManager.KeyguardDismissCallback() {
                @Override
                public void onDismissSucceeded() {
                    call.onAnswer();
                    finish();
                }
            });
            return;
        }
        call.onAnswer();
        finish();
    }
}
