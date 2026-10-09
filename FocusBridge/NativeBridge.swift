import AVFoundation
import UIKit
import WebKit

/// What the page can ask the app to do, as `window.FBNative`. The names are
/// the ones the Android app uses, so the website treats both apps alike:
///
///     speak(text, rate, pitch), stop()   read-aloud in the device's own voice
///     saveFile(name, mime, text)         "Download my data", the teacher's export
///     copyText(text)                     the copy buttons
///
/// `userScript` defines `window.FBNative` before any of the page's own
/// scripts run, and its calls arrive here as messages on the "fbnative"
/// handler. Messages are honoured only from the site's own main frame.
///
/// This works with every version of the website, including ones that know
/// nothing about the app. The script also:
///   * replaces `window.speechSynthesis`, so read-aloud uses the voice below
///     (heard even on silent; see FocusBridgeApp), and
///   * catches the page's "download" links (a blob URL + `a.click()`), which
///     a web view would otherwise ignore, and sends them to `saveFile`.
@MainActor
final class NativeBridge: NSObject, WKScriptMessageHandler {
    static let handlerName = "fbnative"

    /// Shows the grown-up check and the share sheet. Weak: the controller owns us.
    weak var presenter: UIViewController?

    private let synthesizer = AVSpeechSynthesizer()

    static var userScript: WKUserScript {
        WKUserScript(source: script, injectionTime: .atDocumentStart, forMainFrameOnly: true)
    }

    // MARK: - Messages from the page

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.frameInfo.isMainFrame,
              SiteConfig.isSiteOrigin(message.frameInfo.securityOrigin),
              let body = message.body as? [String: Any],
              let op = body["op"] as? String else { return }

        switch op {
        case "speak":
            speak(body["text"] as? String ?? "",
                  rate: (body["rate"] as? NSNumber)?.floatValue ?? 1,
                  pitch: (body["pitch"] as? NSNumber)?.floatValue ?? 1)
        case "stop":
            stopSpeaking()
        case "saveFile":
            saveFile(named: body["name"] as? String ?? "", text: body["text"] as? String ?? "")
        case "copyText":
            UIPasteboard.general.string = body["text"] as? String ?? ""
        default:
            break
        }
    }

    // MARK: - Read-aloud

    func speak(_ text: String, rate: Float, pitch: Float) {
        let words = String(text.prefix(2000))
        guard !words.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        synthesizer.stopSpeaking(at: .immediate)

        let utterance = AVSpeechUtterance(string: words)
        // The web's rate 1.0 is "normal"; AVSpeech's normal is its default rate.
        let scaled = AVSpeechUtteranceDefaultSpeechRate * clamp(rate)
        utterance.rate = min(max(scaled, AVSpeechUtteranceMinimumSpeechRate), AVSpeechUtteranceMaximumSpeechRate)
        utterance.pitchMultiplier = clamp(pitch)
        utterance.voice = AVSpeechSynthesisVoice(language: AVSpeechSynthesisVoice.currentLanguageCode())
        synthesizer.speak(utterance)
    }

    func stopSpeaking() {
        synthesizer.stopSpeaking(at: .immediate)
    }

    private func clamp(_ value: Float) -> Float {
        value.isFinite ? min(max(value, 0.5), 2) : 1
    }

    // MARK: - Saving a file

    /// Writes the file to the app's temporary folder, then - past the
    /// grown-up check - offers the share sheet, whose "Save to Files" puts it
    /// wherever the person chooses.
    private func saveFile(named name: String, text: String) {
        guard let presenter else { return }
        let cleaned = name.replacingOccurrences(of: "[^A-Za-z0-9._-]", with: "-", options: .regularExpression)
        let fileName = cleaned.isEmpty ? "focus-bridge.json" : cleaned
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(fileName)
        do {
            try text.write(to: url, atomically: true, encoding: .utf8)
        } catch {
            return
        }

        ParentalGate.ask(from: presenter, reason: "This saves a copy of the Focus Bridge data, or sends it somewhere.") { [weak presenter] in
            guard let presenter else { return }
            let share = UIActivityViewController(activityItems: [url], applicationActivities: nil)
            if let popover = share.popoverPresentationController {   // iPad: a share sheet must have an anchor
                popover.sourceView = presenter.view
                popover.sourceRect = CGRect(x: presenter.view.bounds.midX, y: presenter.view.bounds.midY,
                                            width: 0, height: 0)
                popover.permittedArrowDirections = []
            }
            presenter.present(share, animated: true)
        }
    }

    // MARK: - The script the page sees

    private static let script = #"""
    (function () {
      if (window.FBNative) return;
      function post(op, fields) {
        var message = fields || {};
        message.op = op;
        window.webkit.messageHandlers.fbnative.postMessage(message);
      }
      function str(value) { return String(value == null ? "" : value); }

      window.FBNative = {
        platform: "ios",
        speak: function (text, rate, pitch) { post("speak", { text: str(text), rate: +rate || 1, pitch: +pitch || 1 }); },
        stop: function () { post("stop"); },
        saveFile: function (name, mime, text) {
          post("saveFile", { name: str(name) || "focus-bridge.json", mime: str(mime) || "application/json", text: str(text) });
        },
        copyText: function (text) { post("copyText", { text: str(text) }); }
      };

      // Read-aloud: the page calls speechSynthesis.speak(new SpeechSynthesisUtterance(text)).
      // Route it to the device's own voice, which ignores the silent switch.
      var speech = {
        speaking: false, pending: false, paused: false,
        speak: function (utterance) {
          if (!utterance) return;
          window.FBNative.speak(utterance.text, utterance.rate, utterance.pitch);
        },
        cancel: function () { window.FBNative.stop(); },
        pause: function () {}, resume: function () {},
        getVoices: function () { return []; },
        addEventListener: function () {}, removeEventListener: function () {}
      };
      try {
        Object.defineProperty(window, "speechSynthesis", { value: speech, configurable: true, writable: true });
      } catch (e) {}
      if (typeof window.SpeechSynthesisUtterance !== "function") {
        window.SpeechSynthesisUtterance = function (text) { this.text = str(text); this.rate = 1; this.pitch = 1; };
      }

      // Downloads: the page makes a blob URL and clicks a link to it, which a
      // web view ignores. Read the blob and hand it to saveFile instead.
      var click = HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click = function () {
        if (this.hasAttribute("download") && /^blob:/.test(this.href)) {
          var name = this.getAttribute("download") || "focus-bridge.json";
          fetch(this.href)
            .then(function (response) { return response.text(); })
            .then(function (text) { window.FBNative.saveFile(name, "application/json", text); })
            .catch(function () {});
          return;
        }
        return click.apply(this, arguments);
      };
    })();
    """#
}
