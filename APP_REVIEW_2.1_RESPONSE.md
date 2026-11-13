# App Review — Guideline 2.1 "Information Needed" response (FINAL)

Standard request for new developer accounts. Steps:
1. Paste the text block below into App Store Connect **Resolution Center** reply.
2. Attach the demo video: `~/Desktop/FocusBridge-Screenshots/FocusBridge-demo-v2-480p.mov` (15 MB).
3. Paste the same text block into **App Review Information → Notes** field.
4. Confirm the video shows ACCOUNT DELETION before sending.

Demo credentials: teacher username `test1` / password `testtest`; class code `9J53PB`.

---

## Reply text (paste into Resolution Center AND Notes)

```
Thank you for reviewing Focus Bridge. Please find the requested information below.

A screen recording demonstrating the full user flow is attached to this reply.

2. PURPOSE & TARGET AUDIENCE
Focus Bridge helps special-education students understand their feelings, follow a picture schedule, use calming tools, and read social stories, and it helps their teachers see patterns over time. The target audience is special-education students (roughly ages 6–12), their teachers, and parents. It solves the problem of giving non-verbal and neurodivergent children a simple, distraction-free way to communicate how they feel and stay on routine, while giving teachers insight into each child's day. It is publicly useful: any family can use the at-home mode, and any teacher can create a classroom. It is not limited to a single organization.

3. SETUP & ACCESS INSTRUCTIONS
- No login is required for the core at-home experience. On launch, tap "Start Here!" to use feelings check-ins, the picture schedule, the calm corner, and social stories. All of this data stays on the device.
- Classroom features require a teacher sign-in. Demo teacher account:
    Username: test1
    Password: testtest
  After signing in via the "Teachers" button, you can view the demo classroom and its Insights tab.
- To see the student side, tap "Students" and enter class code: 9J53PB
- Native features: read-aloud uses the device's own voice (also audible with the ringer switch on silent); "Download my data" saves via the share sheet; a parental gate ("What is a × b?") guards every link out of the app and the share sheet; the app works offline after first launch.
- Account deletion: a teacher can delete their account and all classrooms in Settings → Delete my account. A student can delete their own data from their settings.

4. EXTERNAL SERVICES USED
- Supabase — PostgreSQL database and authentication, used only in Classroom mode to store classroom data and sign in teachers and students.
- Web hosting for focusbridge.org — the app is a native shell around our own single-page web app, which we operate.
- Apple AVSpeechSynthesizer (on-device) for read-aloud.
There are no third-party analytics, advertising, tracking SDKs, payment processors, or AI services. The app is free with no in-app purchases.

5. REGIONAL DIFFERENCES
There are no regional differences. The app functions consistently across all regions. Content is in English and no features are region-locked.

6. REGULATED INDUSTRY / PROTECTED MATERIAL
The app contains only our own content and uses no protected third-party material. Because it is used by children, we follow child-privacy practices: no tracking, ads, or third-party analytics; a parental gate on every exit and on data export; teacher- and school-mediated consent for classroom mode; and on-device-only data in home mode. Our privacy policy is available at https://focusbridge.org/privacy.html.
```
