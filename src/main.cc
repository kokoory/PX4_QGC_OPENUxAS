#include "QGCApplication.h"
#include "QGCCommandLineParser.h"
#include "QGCLogging.h"
#include "QGCLoggingCategory.h"
#include "Platform.h"

#ifdef QGC_CESIUM3D_ENABLED
    #include <QtWebEngineQuick/QtWebEngineQuick>
#endif

#ifdef QGC_UNITTEST_BUILD
    #include "UnitTestList.h"
#endif

QGC_LOGGING_CATEGORY_ON(MainLog, "Main")

int main(int argc, char *argv[])
{
    // --- Parse command line arguments ---
    const auto args = QGCCommandLineParser::parse(argc, argv);
    if (const auto exitCode = QGCCommandLineParser::handleParseResult(args)) {
        return *exitCode;
    }

#ifdef QGC_CESIUM3D_ENABLED
    // --- WebEngine initialization (must be called before QApplication) ---
    // On Ubuntu 24.04+ kernel.apparmor_restrict_unprivileged_userns=1 blocks the
    // Chromium sandbox when QtWebEngineProcess lives outside an AppArmor profile
    // (e.g. a user-installed Qt under $HOME). Disable the sandbox unless the user
    // has set their own value, otherwise the GPU/renderer process crashes on spawn.
#ifdef Q_OS_LINUX
    if (!qEnvironmentVariableIsSet("QTWEBENGINE_DISABLE_SANDBOX")
        && !qEnvironmentVariableIsSet("QTWEBENGINE_CHROMIUM_FLAGS")) {
        qputenv("QTWEBENGINE_DISABLE_SANDBOX", "1");
    }
#endif
    QtWebEngineQuick::initialize();
#endif

    // --- Platform initialization ---
    if (const auto exitCode = Platform::initialize(argc, argv, args)) {
        return *exitCode;
    }

    QGCApplication app(argc, argv, args);

    QGCLogging::installHandler();

    Platform::setupPostApp();

    app.init();

    // --- Run application or tests ---
    const auto run = [&]() -> int {
        using QGCCommandLineParser::AppMode;
        switch (QGCCommandLineParser::determineAppMode(args)) {
#ifdef QGC_UNITTEST_BUILD
        case AppMode::ListTests:
        case AppMode::Test:
            return QGCUnitTest::handleTestOptions(args);
#endif
        case AppMode::BootTest:
            if (!app.bootTestPassed()) {
                qCCritical(MainLog) << "Simple boot test failed during GStreamer initialization";
                return 1;
            }
            qCInfo(MainLog) << "Simple boot test completed";
            return 0;
        case AppMode::Gui:
            qCInfo(MainLog) << "Starting application event loop";
            return app.exec();
        }
        Q_UNREACHABLE();
    };

    const int exitCode = run();

    // --- Cleanup ---
    app.shutdown();

    qCInfo(MainLog) << "Exiting main";
    return exitCode;
}
