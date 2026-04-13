#pragma once

#include <QObject>
#include <QUdpSocket>
#include <QJsonObject>
#include <QJsonDocument>
#include <QDateTime>
#include <QHostAddress>
#include <QtQmlIntegration/QtQmlIntegration>

/// Broadcasts UI events over UDP so external programs can monitor QGC interactions.
/// Listens on a configurable port (default 45678) for external commands.
///
/// Usage from QML:
///   EventBroadcaster.sendEvent("button", "arm_clicked", {"vehicle": 1})
///   EventBroadcaster.sendEvent("view", "fly_view_opened", {})
///
/// External programs receive JSON on UDP port 45678:
///   {"timestamp": 1234567890.123, "category": "button", "event": "arm_clicked", "data": {"vehicle": 1}}
class EventBroadcaster : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

public:
    explicit EventBroadcaster(QObject *parent = nullptr);
    ~EventBroadcaster();

    static EventBroadcaster *instance();

    /// Send a UI event to all listeners
    /// @param category  Event category (e.g., "button", "view", "setting", "map", "mission")
    /// @param event     Event name (e.g., "arm_clicked", "fly_view_opened")
    /// @param data      Additional event data as QVariantMap (optional)
    Q_INVOKABLE void sendEvent(const QString &category, const QString &event, const QVariantMap &data = {});

    /// Set the broadcast port (default: 45678)
    Q_INVOKABLE void setBroadcastPort(quint16 port);

    /// Enable/disable broadcasting
    Q_INVOKABLE void setEnabled(bool enabled);
    Q_INVOKABLE bool isEnabled() const { return _enabled; }

signals:
    void eventSent(const QString &category, const QString &event);

private:
    void _broadcast(const QByteArray &data);

    static EventBroadcaster *_instance;
    QUdpSocket *_socket = nullptr;
    quint16 _broadcastPort = 45678;
    bool _enabled = true;
    quint64 _eventCounter = 0;
};
