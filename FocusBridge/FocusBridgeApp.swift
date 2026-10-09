import AVFoundation
import SwiftUI

/// Focus Bridge for iPhone and iPad: one screen showing https://focusbridge.org.
///
/// The app is the website plus a few native helpers the page can call -
/// read-aloud, saving a file, copying - so that it works like an app rather
/// than a bookmark. HANDOFF.md, next to this project, explains the design.
@main
struct FocusBridgeApp: App {
    init() {
        // Read-aloud must be heard even with the ringer switch on silent: a
        // child who relies on it will not know the switch exists.
        try? AVAudioSession.sharedInstance().setCategory(.playback, mode: .spokenAudio, options: [.mixWithOthers])
    }

    var body: some Scene {
        WindowGroup {
            WebAppScreen()
                .ignoresSafeArea()   // WebViewController lays itself out inside the safe area
        }
    }
}

/// The UIKit screen, hosted in SwiftUI's app lifecycle. That lifecycle is
/// scene-based, which current iOS versions require of every app.
struct WebAppScreen: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> WebViewController {
        WebViewController()
    }

    func updateUIViewController(_ controller: WebViewController, context: Context) {}
}
