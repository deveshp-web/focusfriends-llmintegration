import UIKit
import WebKit

/// Everything that names the website, in one place.
///
/// If the site ever moves, change it here AND in Config/Info.plist
/// (WKAppBoundDomains): the web view refuses to load a domain that is not
/// listed there.
enum SiteConfig {
    /// The page the app opens.
    static let startURL = URL(string: "https://focusbridge.org/")!

    /// Pages on these hosts stay in the app. Anything else is "outside".
    static let hosts: Set<String> = ["focusbridge.org", "www.focusbridge.org"]

    /// The site's own colours, for the parts of the screen the page doesn't cover.
    static let pageColor = UIColor(red: 1.0, green: 0.973, blue: 0.941, alpha: 1)    // #FFF8F0
    static let brandGreen = UIColor(red: 0.153, green: 0.682, blue: 0.376, alpha: 1) // #27AE60

    /// A page of the site itself.
    static func isSitePage(_ url: URL?) -> Bool {
        guard let url, url.scheme?.lowercased() == "https",
              let host = url.host?.lowercased() else { return false }
        return hosts.contains(host)
    }

    /// The frame a bridge message came from is the site itself, over HTTPS.
    static func isSiteOrigin(_ origin: WKSecurityOrigin) -> Bool {
        origin.`protocol`.lowercased() == "https" && hosts.contains(origin.host.lowercased())
    }
}
