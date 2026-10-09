import UIKit

/// "Ask a grown-up": a question a young child can't answer, in front of
/// anything that leaves the app.
///
/// App Review requires this in a Kids Category app before any link out of the
/// app (guideline 1.3). Focus Bridge also puts it in front of the share sheet,
/// because sharing is how a file leaves the device.
enum ParentalGate {
    @MainActor
    static func ask(from presenter: UIViewController, reason: String, then action: @escaping () -> Void) {
        guard presenter.presentedViewController == nil else { return }   // one question at a time
        let a = Int.random(in: 6...9)
        let b = Int.random(in: 6...9)
        let alert = UIAlertController(
            title: "Ask a grown-up",
            message: "\(reason)\n\nWhat is \(a) × \(b)?",
            preferredStyle: .alert)
        alert.addTextField { field in
            field.keyboardType = .numberPad
            field.placeholder = "Answer"
        }
        alert.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        alert.addAction(UIAlertAction(title: "Continue", style: .default) { [weak alert] _ in
            let answer = alert?.textFields?.first?.text?.trimmingCharacters(in: .whitespaces) ?? ""
            if Int(answer) == a * b {
                action()
            }
        })
        presenter.present(alert, animated: true)
    }
}
