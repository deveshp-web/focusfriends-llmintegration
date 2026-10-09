import UIKit

/// Shown when the site can't be reached and nothing is cached yet. In
/// practice that means the first launch without internet: after one visit
/// online, the site's service worker keeps a copy and the app opens offline.
final class OfflineView: UIView {
    var onRetry: (() -> Void)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = SiteConfig.pageColor

        let face = UILabel()
        face.text = "🌤️"
        face.font = .systemFont(ofSize: 64)
        face.isAccessibilityElement = false

        let title = UILabel()
        title.text = "No internet yet"
        title.font = .systemFont(ofSize: 22, weight: .bold)
        title.textColor = UIColor(red: 0.173, green: 0.243, blue: 0.314, alpha: 1)   // #2C3E50

        let body = UILabel()
        body.text = "Focus Bridge needs the internet the first time it opens. After that, it keeps working without it."
        body.font = .systemFont(ofSize: 16)
        body.textColor = .secondaryLabel
        body.numberOfLines = 0
        body.textAlignment = .center

        var config = UIButton.Configuration.filled()
        config.title = "Try again"
        config.baseBackgroundColor = SiteConfig.brandGreen
        config.cornerStyle = .large
        config.contentInsets = NSDirectionalEdgeInsets(top: 14, leading: 36, bottom: 14, trailing: 36)
        let retry = UIButton(configuration: config, primaryAction: UIAction { [weak self] _ in
            self?.onRetry?()
        })

        let stack = UIStackView(arrangedSubviews: [face, title, body, retry])
        stack.axis = .vertical
        stack.alignment = .center
        stack.spacing = 12
        stack.setCustomSpacing(24, after: body)
        stack.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor),
            stack.leadingAnchor.constraint(greaterThanOrEqualTo: leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(lessThanOrEqualTo: trailingAnchor, constant: -24),
            stack.widthAnchor.constraint(lessThanOrEqualToConstant: 380),
        ])
    }

    required init?(coder: NSCoder) {
        fatalError("OfflineView is built in code")
    }
}
