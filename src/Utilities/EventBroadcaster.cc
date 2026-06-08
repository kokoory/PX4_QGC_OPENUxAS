#include "EventBroadcaster.h"
#include "QGCLoggingCategory.h"

QGC_LOGGING_CATEGORY(EventBroadcasterLog, "EventBroadcaster")

EventBroadcaster *EventBroadcaster::_instance = nullptr;

EventBroadcaster::EventBroadcaster(QObject *parent)
    : QObject(parent)
    , _socket(new QUdpSocket(this))
    , _receiveSocket(new QUdpSocket(this))
{
    _instance = this;

    _bindReceiveSocket();
    connect(_receiveSocket, &QUdpSocket::readyRead, this, &EventBroadcaster::_onReadyRead);

    qCDebug(EventBroadcasterLog) << "EventBroadcaster initialized, broadcasting on port" << _broadcastPort
                                 << ", receiving on port" << _receivePort;
}

EventBroadcaster::~EventBroadcaster()
{
    if (_instance == this) {
        _instance = nullptr;
    }
}

EventBroadcaster *EventBroadcaster::instance()
{
    return _instance;
}

void EventBroadcaster::sendEvent(const QString &category, const QString &event, const QVariantMap &data)
{
    if (!_enabled) {
        return;
    }

    _eventCounter++;

    QJsonObject json;
    json["seq"] = static_cast<qint64>(_eventCounter);
    json["timestamp"] = QDateTime::currentMSecsSinceEpoch() / 1000.0;
    json["category"] = category;
    json["event"] = event;

    if (!data.isEmpty()) {
        json["data"] = QJsonObject::fromVariantMap(data);
    }

    const QByteArray payload = QJsonDocument(json).toJson(QJsonDocument::Compact) + "\n";
    _broadcast(payload);

    qCDebug(EventBroadcasterLog) << "Event:" << category << event;
    emit eventSent(category, event);
}

void EventBroadcaster::setBroadcastPort(quint16 port)
{
    _broadcastPort = port;
    qCDebug(EventBroadcasterLog) << "Broadcast port changed to" << port;
}

void EventBroadcaster::setReceivePort(quint16 port)
{
    if (_receivePort == port) {
        return;
    }
    _receivePort = port;
    _bindReceiveSocket();
    qCDebug(EventBroadcasterLog) << "Receive port changed to" << port;
}

void EventBroadcaster::setEnabled(bool enabled)
{
    _enabled = enabled;
    qCDebug(EventBroadcasterLog) << "Broadcasting" << (enabled ? "enabled" : "disabled");
}

void EventBroadcaster::_onReadyRead()
{
    while (_receiveSocket->hasPendingDatagrams()) {
        QByteArray datagram;
        datagram.resize(_receiveSocket->pendingDatagramSize());
        QHostAddress sender;
        quint16 senderPort;
        _receiveSocket->readDatagram(datagram.data(), datagram.size(), &sender, &senderPort);

        qCDebug(EventBroadcasterLog) << "Received datagram from" << sender.toString() << ":" << senderPort
                                     << "size:" << datagram.size();

        const QJsonDocument doc = QJsonDocument::fromJson(datagram);
        if (!doc.isObject()) {
            qCWarning(EventBroadcasterLog) << "Received invalid JSON command:" << datagram;
            continue;
        }

        const QJsonObject json = doc.object();
        const QString action = json.value("action").toString();
        if (action.isEmpty()) {
            qCWarning(EventBroadcasterLog) << "Received command without 'action' field:" << datagram;
            continue;
        }

        // Build params map from all fields except "action"
        QVariantMap params;
        for (auto it = json.constBegin(); it != json.constEnd(); ++it) {
            if (it.key() != "action") {
                params.insert(it.key(), it.value().toVariant());
            }
        }

        qCDebug(EventBroadcasterLog) << "Command received - action:" << action << "params:" << params;
        emit commandReceived(action, params);
    }
}

void EventBroadcaster::_bindReceiveSocket()
{
    _receiveSocket->close();
    if (!_receiveSocket->bind(QHostAddress::Any, _receivePort, QUdpSocket::ShareAddress | QUdpSocket::ReuseAddressHint)) {
        qCWarning(EventBroadcasterLog) << "Failed to bind receive socket to port" << _receivePort
                                       << ":" << _receiveSocket->errorString();
    } else {
        qCDebug(EventBroadcasterLog) << "Receive socket bound to port" << _receivePort;
    }
}

void EventBroadcaster::_broadcast(const QByteArray &data)
{
    _socket->writeDatagram(data, QHostAddress::Broadcast, _broadcastPort);
    _socket->writeDatagram(data, QHostAddress::LocalHost, _broadcastPort);
}

void EventBroadcaster::setPlanOverlay(const QVariant &overlay)
{
    _planOverlay = overlay;
    emit planOverlayChanged();
}
