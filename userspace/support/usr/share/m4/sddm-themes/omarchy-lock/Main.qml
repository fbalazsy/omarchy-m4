import QtQuick
import QtQuick.Effects

// Same wallpaper, Tokyo Night colors, dimensions and password presentation as
// the installed Omarchy LockView. Authentication remains SDDM's normal PAM flow.
Rectangle {
    id: root
    // Request RGBA8888 before SDDM shows the window; root stays opaque.
    Window.onWindowChanged: if (Window.window) Window.window.color = "transparent"
    width: 1728; height: 1117
    color: "#1a1b26"
    property bool checking: false
    property string failure: ""
    property string account: userModel.lastUser !== "" ? userModel.lastUser : userModel.data(userModel.index(0, 0), Qt.UserRole + 1)
    function submit() {
        if (checking || password.text.length === 0) return
        if (sessionModel.count !== 1) { failure = "Session configuration changed"; return }
        checking = true
        var secret = password.text
        password.text = ""
        sddm.login(account, secret, 0)
        secret = ""
    }
    Image {
        id: wallpaper
        anchors.fill: parent
        source: "background.jpg"
        fillMode: Image.PreserveAspectCrop
        sourceSize.width: width
        sourceSize.height: height
    }
    MultiEffect {
        anchors.fill: wallpaper
        source: wallpaper
        autoPaddingEnabled: false
        blurEnabled: wallpaper.status === Image.Ready
        blur: 1.0
        blurMax: 128
        blurMultiplier: 1.25
        contrast: -0.08
    }
    MouseArea {
        anchors.fill: parent
        onClicked: password.forceActiveFocus()
    }
    TextMetrics {
        id: dots
        font.family: "monospace"
        font.pixelSize: 21
        font.letterSpacing: 3
        text: "●".repeat(password.text.length)
    }
    Rectangle {
        id: field
        width: 381; height: 67
        anchors.centerIn: parent
        color: "#cc1a1b26"
        border.color: "#a9b1d6"
        border.width: 3
        clip: true
        TextInput {
            id: password
            objectName: "password"
            anchors.fill: parent
            anchors.margins: 3
            anchors.leftMargin: 21
            anchors.rightMargin: 21
            focus: true
            enabled: !root.checking
            readOnly: root.checking
            verticalAlignment: TextInput.AlignVCenter
            horizontalAlignment: TextInput.AlignHCenter
            echoMode: TextInput.Password
            passwordCharacter: "●"
            passwordMaskDelay: 0
            color: "#a9b1d6"
            selectionColor: "#737aa2f7"
            selectedTextColor: "#a9b1d6"
            font.family: "monospace"
            font.pixelSize: text.length > 0 ? Math.max(1, Math.floor(21 * Math.min(1, (width - 4) / Math.max(1, dots.advanceWidth)))) : 18
            font.letterSpacing: text.length > 0 ? 3 * Math.min(1, (width - 4) / Math.max(1, dots.advanceWidth)) : 0
            cursorVisible: activeFocus && !root.checking && text.length > 0
            onTextChanged: if (text.length > 0) root.failure = ""
            onAccepted: root.submit()
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Escape || (event.modifiers & Qt.ControlModifier && event.key === Qt.Key_U)) {
                    text = ""
                    event.accepted = true
                }
            }
        }
        Text {
            anchors.fill: password
            textFormat: Text.PlainText
            text: root.checking ? "Checking…" : (root.failure || "Enter Password")
            visible: password.text.length === 0
            color: "#a9b1d6"
            font.family: "monospace"
            font.pixelSize: 18
            font.italic: root.failure.length > 0
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
    Connections {
        target: sddm
        function onLoginFailed() {
            root.checking = false
            root.failure = "Wrong password"
            password.forceActiveFocus()
        }
        function onLoginSucceeded() { root.checking = true }
    }
    Component.onCompleted: password.forceActiveFocus()
}
