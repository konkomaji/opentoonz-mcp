// OpenToonz MCP bridge: a Qt generic plugin that runs inside OpenToonz 1.8.0 and lets the MCP server draw on
// the drawing that is open in the viewer, while I keep drawing on it too.
//
// OpenToonz has no command channel of its own, so this plugin adds one: a local socket ("otmcp-bridge") that
// takes one JSON object per line and answers with one JSON object per line. Every command runs on the GUI
// thread, goes through the same calls the OpenToonz tools use, and registers an undo step, so Ctrl+Z in
// OpenToonz takes back what the assistant drew.
//
// How it gets loaded: it is an image-format plugin in <OpenToonz>/imageformats that registers for "png" and
// then says it cannot read anything, so Qt goes on to its real PNG reader. Qt loads it the first time
// OpenToonz reads a PNG, which happens while it starts. The bridge then starts once, on the GUI thread.
// (A generic plugin would be the obvious kind, but the Qt that OpenToonz 1.8.0 ships does not create generic
// plugins from QT_QPA_GENERICPLUGINS or -plugin; I measured that.) Set OTBRIDGE=0 to keep the bridge off.

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QImageIOPlugin>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalServer>
#include <QLocalSocket>
#include <QMutexLocker>
#include <QPointer>

#include <atomic>

#include "tools/tool.h"
#include "tools/toolhandle.h"
#include "toonz/tapplication.h"
#include "toonz/tcolumnhandle.h"
#include "toonz/tframehandle.h"
#include "toonz/toonzscene.h"
#include "toonz/tpalettehandle.h"
#include "toonz/tscenehandle.h"
#include "toonz/txshcell.h"
#include "toonz/txsheet.h"
#include "toonz/txsheethandle.h"
#include "toonz/txshlevel.h"
#include "toonz/txshlevelhandle.h"
#include "toonzqt/menubarcommand.h"
#include "tcolorstyles.h"
#include "tpalette.h"
#include "tstroke.h"
#include "tundo.h"
#include "tvectorimage.h"

#include "otbridge_build.h"  // written by build.py: which OpenToonz build this was compiled against

namespace {

const int kProtocol = 1;

// A small log next to the user's temp files: the only way to see why the bridge is not there.
void logLine(const QString &text) {
  QFile f(QDir::temp().filePath("otbridge.log"));
  if (f.open(QIODevice::Append | QIODevice::Text))
    f.write((QDateTime::currentDateTime().toString(Qt::ISODate) + " " + text + QChar('\n')).toUtf8());
}

QJsonObject fail(const QString &why) { return QJsonObject{{"ok", false}, {"error", why}}; }

// The drawing a command works on: the cell under the cursor, or an explicit column and row (both 0-based).
struct Target {
  TXshLevelP level;
  TFrameId fid;
  TVectorImageP image;
  int col = -1, row = -1;
  QString error;
};

Target resolve(const QJsonObject &cmd, bool create) {
  Target t;
  TApplication *app = TTool::getApplication();
  if (!app) {
    t.error = "OpenToonz is still starting";
    return t;
  }
  TXsheet *xsh = app->getCurrentXsheet()->getXsheet();
  if (!xsh) {
    t.error = "no scene is open";
    return t;
  }
  t.col = cmd.contains("column") ? cmd["column"].toInt() : app->getCurrentColumn()->getColumnIndex();
  t.row = cmd.contains("row") ? cmd["row"].toInt() : app->getCurrentFrame()->getFrame();
  if (cmd.contains("level")) {
    // a drawing named by level and number: the first cell of the xsheet that shows it
    std::wstring name = cmd["level"].toString().toStdWString();
    int number        = cmd["drawing"].toInt();
    bool levelSeen = false, found = false;
    for (int c = 0; c < xsh->getColumnCount() && !found; ++c)
      for (int r = 0; r < xsh->getFrameCount() && !found; ++r) {
        const TXshCell &cell = xsh->getCell(r, c);
        if (cell.isEmpty() || cell.m_level->getName() != name) continue;
        levelSeen = true;
        if (cell.m_frameId.getNumber() == number) {
          t.col = c, t.row = r;
          found = true;
        }
      }
    if (!found) {
      // a new drawing can only be made where the user is: on an empty cell of that level's column
      int cc = app->getCurrentColumn()->getColumnIndex(), cr = app->getCurrentFrame()->getFrame();
      TXshLevel *cur = app->getCurrentLevel()->getLevel();
      bool here = create && cur && cur->getName() == name && cc >= 0 && cr >= 0 && xsh->getCell(cr, cc).isEmpty();
      if (!here) {
        t.error = levelSeen ? QString("drawing %1 of level '%2' is not on the xsheet in OpenToonz. To make it, put "
                                      "the cursor on an empty cell of that column and call again.")
                                  .arg(number).arg(cmd["level"].toString())
                            : QString("level '%1' is not on the xsheet of the scene open in OpenToonz")
                                  .arg(cmd["level"].toString());
        return t;
      }
      t.col = cc, t.row = cr;
      TTool *tool = app->getCurrentTool()->getTool();
      if (tool) tool->touchImage();
    }
  }
  if (t.col < 0 || t.row < 0) {
    t.error = "no cell is selected";
    return t;
  }
  TXshCell cell = xsh->getCell(t.row, t.col);
  if (cell.isEmpty() && create && !cmd.contains("column") && !cmd.contains("row")) {
    // the same thing a drawing tool does on an empty cell: make the drawing (it follows the user's
    // "create new drawings automatically" preference)
    TTool *tool = app->getCurrentTool()->getTool();
    if (tool && tool->touchImage()) cell = xsh->getCell(t.row, t.col);
  }
  if (cell.isEmpty()) {
    t.error = QString("column %1, frame %2 has no drawing").arg(t.col + 1).arg(t.row + 1);
    return t;
  }
  t.level = cell.m_level;
  t.fid   = cell.m_frameId;
  TImageP img = cell.getImage(true);
  t.image     = img;
  if (!t.image) t.error = "that drawing is not a vector drawing (the bridge draws on vector levels only)";
  return t;
}

void changed(const Target &t) {
  TApplication *app = TTool::getApplication();
  if (t.level) t.level->setDirtyFlag(true);
  app->getCurrentLevel()->notifyLevelChange();
  app->getCurrentScene()->notifySceneChanged();
  if (TTool *tool = app->getCurrentTool()->getTool()) tool->invalidate();
}

// One undo step for a batch of added strokes. Strokes are always added on top, so they are the last ones.
class AddStrokesUndo final : public TUndo {
  TXshLevelP m_level;
  TFrameId m_fid;
  TVectorImageP m_image;
  int m_first;
  std::vector<TStroke *> m_copies;

public:
  AddStrokesUndo(const Target &t, int first, const std::vector<TStroke *> &added)
      : m_level(t.level), m_fid(t.fid), m_image(t.image), m_first(first) {
    for (TStroke *s : added) m_copies.push_back(new TStroke(*s));
  }
  ~AddStrokesUndo() {
    for (TStroke *s : m_copies) delete s;
  }
  void notify() const {
    Target t;
    t.level = m_level;
    changed(t);
  }
  void undo() const override {
    QMutexLocker lock(m_image->getMutex());
    for (int i = (int)m_copies.size() - 1; i >= 0; --i)
      if (m_first + i < (int)m_image->getStrokeCount()) m_image->deleteStroke(m_first + i);
    notify();
  }
  void redo() const override {
    QMutexLocker lock(m_image->getMutex());
    for (TStroke *s : m_copies) m_image->addStroke(new TStroke(*s), false);
    notify();
  }
  int getSize() const override { return sizeof(*this) + (int)m_copies.size() * 512; }
  QString getHistoryString() override {
    return QString("Assistant: %1 stroke(s) on %2").arg(m_copies.size()).arg(QString::fromStdWString(m_level->getName()));
  }
};

// One undo step for erased strokes: index and a copy of each (with its group), in ascending index order.
class EraseStrokesUndo final : public TUndo {
  TXshLevelP m_level;
  TVectorImageP m_image;
  std::vector<std::pair<int, VIStroke *>> m_gone;

public:
  EraseStrokesUndo(const Target &t, const std::vector<std::pair<int, VIStroke *>> &gone)
      : m_level(t.level), m_image(t.image), m_gone(gone) {}
  ~EraseStrokesUndo() {
    for (auto &g : m_gone) deleteVIStroke(g.second);
  }
  void notify() const {
    Target t;
    t.level = m_level;
    changed(t);
  }
  void undo() const override {
    QMutexLocker lock(m_image->getMutex());
    for (auto &g : m_gone) m_image->insertStrokeAt(cloneVIStroke(g.second), g.first);
    notify();
  }
  void redo() const override {
    QMutexLocker lock(m_image->getMutex());
    for (int i = (int)m_gone.size() - 1; i >= 0; --i) m_image->deleteStroke(m_gone[i].first);
    notify();
  }
  int getSize() const override { return sizeof(*this) + (int)m_gone.size() * 512; }
  QString getHistoryString() override {
    return QString("Assistant: erase %1 stroke(s) on %2").arg(m_gone.size()).arg(QString::fromStdWString(m_level->getName()));
  }
};

// The palette style to use for a colour: an existing solid style of exactly that colour, else a new one.
int styleFor(TPalette *palette, const QJsonArray &rgba, bool &added) {
  TPixel32 want(rgba.at(0).toInt(), rgba.at(1).toInt(), rgba.at(2).toInt(), rgba.size() > 3 ? rgba.at(3).toInt() : 255);
  for (int i = 1; i < palette->getStyleCount(); ++i) {
    TColorStyle *cs = palette->getStyle(i);
    if (cs && cs->getTagId() == 3 && cs->getMainColor() == want) return i;
  }
  added = true;
  return palette->getPage(0)->addStyle(want);
}

QJsonObject opContext() {
  TApplication *app = TTool::getApplication();
  if (!app) return fail("OpenToonz is still starting");
  QJsonObject r{{"ok", true}};
  ToonzScene *scene = app->getCurrentScene()->getScene();
  if (scene) {
    r["scene"]      = QString::fromStdWString(scene->getSceneName());
    r["scene_path"] = QString::fromStdWString(scene->getScenePath().getWideString());
    r["unsaved"]    = app->getCurrentScene()->getDirtyFlag();
  }
  int col = app->getCurrentColumn()->getColumnIndex(), row = app->getCurrentFrame()->getFrame();
  r["column"] = col;
  r["row"]    = row;
  TXsheet *xsh = app->getCurrentXsheet()->getXsheet();
  if (xsh && col >= 0 && row >= 0) {
    TXshCell cell = xsh->getCell(row, col);
    if (!cell.isEmpty()) {
      r["level"]   = QString::fromStdWString(cell.m_level->getName());
      r["drawing"] = cell.m_frameId.getNumber();
      TImageP img  = cell.getImage(false);
      TVectorImageP vi = img;
      r["vector"]  = (bool)vi;
      if (vi) r["strokes"] = (int)vi->getStrokeCount();
    }
  }
  return r;
}

QJsonObject opRead(const QJsonObject &cmd) {
  Target t = resolve(cmd, false);
  if (!t.error.isEmpty()) return fail(t.error);
  QJsonArray strokes;
  TPalette *palette = t.image->getPalette();
  QMutexLocker lock(t.image->getMutex());
  for (UINT i = 0; i < t.image->getStrokeCount(); ++i) {
    TStroke *s = t.image->getStroke(i);
    QJsonArray pts;
    for (int k = 0; k < s->getControlPointCount(); ++k) {
      TThickPoint p = s->getControlPoint(k);
      pts.append(QJsonArray{p.x, p.y, p.thick});
    }
    QJsonObject o{{"index", (int)i}, {"style", s->getStyle()}, {"loop", s->isSelfLoop()}, {"points", pts}};
    TColorStyle *cs = palette ? palette->getStyle(s->getStyle()) : nullptr;
    if (cs && cs->getTagId() == 3) {
      TPixel32 c = cs->getMainColor();
      o["color"] = QJsonArray{c.r, c.g, c.b, c.m};
    }
    strokes.append(o);
  }
  return QJsonObject{{"ok", true},
                     {"level", QString::fromStdWString(t.level->getName())},
                     {"drawing", t.fid.getNumber()},
                     {"column", t.col},
                     {"row", t.row},
                     {"strokes", strokes}};
}

QJsonObject opStroke(const QJsonObject &cmd) {
  QJsonArray list = cmd["strokes"].toArray();
  if (list.isEmpty()) return fail("strokes is empty");
  Target t = resolve(cmd, true);
  if (!t.error.isEmpty()) return fail(t.error);
  TPalette *palette = t.image->getPalette();
  if (!palette) return fail("the level has no palette");
  std::vector<TStroke *> made;
  bool paletteGrew = false;
  for (const QJsonValue &v : list) {
    QJsonObject so = v.toObject();
    QJsonArray pa  = so["points"].toArray();
    if (pa.size() < 3 || pa.size() % 2 == 0) {
      for (TStroke *s : made) delete s;
      return fail("a stroke needs an odd number of control points, at least 3");
    }
    std::vector<TThickPoint> pts;
    for (const QJsonValue &pv : pa) {
      QJsonArray p = pv.toArray();
      pts.push_back(TThickPoint(p.at(0).toDouble(), p.at(1).toDouble(), p.at(2).toDouble()));
    }
    TStroke *s = new TStroke(pts);
    int style  = so.contains("color") ? styleFor(palette, so["color"].toArray(), paletteGrew) : so["style"].toInt(1);
    s->setStyle(style);
    if (so["loop"].toBool()) s->setSelfLoop(true);
    made.push_back(s);
  }
  int first;
  QJsonArray indices;
  {
    QMutexLocker lock(t.image->getMutex());
    first = (int)t.image->getStrokeCount();
    for (TStroke *s : made) indices.append(t.image->addStroke(s, false));
  }
  TUndoManager::manager()->add(new AddStrokesUndo(t, first, made));
  if (paletteGrew) TTool::getApplication()->getCurrentPalette()->notifyPaletteChanged();
  changed(t);
  return QJsonObject{{"ok", true},
                     {"level", QString::fromStdWString(t.level->getName())},
                     {"drawing", t.fid.getNumber()},
                     {"column", t.col},
                     {"row", t.row},
                     {"indices", indices},
                     {"strokes_in_drawing", (int)t.image->getStrokeCount()}};
}

QJsonObject opErase(const QJsonObject &cmd) {
  Target t = resolve(cmd, false);
  if (!t.error.isEmpty()) return fail(t.error);
  std::vector<int> idx;
  for (const QJsonValue &v : cmd["indices"].toArray()) idx.push_back(v.toInt());
  std::sort(idx.begin(), idx.end());
  idx.erase(std::unique(idx.begin(), idx.end()), idx.end());
  if (idx.empty()) return fail("indices is empty");
  std::vector<std::pair<int, VIStroke *>> gone;
  {
    QMutexLocker lock(t.image->getMutex());
    if (idx.front() < 0 || idx.back() >= (int)t.image->getStrokeCount())
      return fail(QString("the drawing has %1 strokes").arg(t.image->getStrokeCount()));
    for (int i : idx) gone.push_back({i, cloneVIStroke(t.image->getVIStroke(i))});
    for (int k = (int)idx.size() - 1; k >= 0; --k) t.image->deleteStroke(idx[k]);
  }
  TUndoManager::manager()->add(new EraseStrokesUndo(t, gone));
  changed(t);
  return QJsonObject{{"ok", true}, {"erased", (int)idx.size()}, {"strokes_in_drawing", (int)t.image->getStrokeCount()}};
}

QJsonObject run(const QJsonObject &cmd) {
  QString op = cmd["op"].toString();
  if (op == "ping")
    return QJsonObject{{"ok", true}, {"bridge", kProtocol}, {"app", "OpenToonz"}, {"built_for", OTBRIDGE_HOST_LABEL}};
  if (op == "context") return opContext();
  if (op == "read") return opRead(cmd);
  if (op == "stroke") return opStroke(cmd);
  if (op == "erase") return opErase(cmd);
  if (op == "save") {  // File > Save All, so tools that read the files see what is on screen
    CommandManager::instance()->execute("MI_SaveAll");
    return QJsonObject{{"ok", true}};
  }
  if (op == "undo" || op == "redo") {  // the same Edit > Undo / Redo the user has
    CommandManager::instance()->execute(op == "undo" ? "MI_Undo" : "MI_Redo");
    return QJsonObject{{"ok", true}};
  }
  return fail("unknown op '" + op + "'");
}

class Bridge final : public QObject {
  QLocalServer m_server;

public:
  explicit Bridge(QObject *parent) : QObject(parent) {
    QLocalServer::removeServer("otmcp-bridge");
    m_server.setSocketOptions(QLocalServer::UserAccessOption);  // this Windows user only
    connect(&m_server, &QLocalServer::newConnection, this, [this]() {
      while (QLocalSocket *sock = m_server.nextPendingConnection()) {
        connect(sock, &QLocalSocket::disconnected, sock, &QObject::deleteLater);
        connect(sock, &QLocalSocket::readyRead, this, [sock]() {
          while (sock->canReadLine()) {
            QByteArray line = sock->readLine().trimmed();
            if (line.isEmpty()) continue;
            QJsonParseError err;
            QJsonDocument doc = QJsonDocument::fromJson(line, &err);
            QJsonObject out;
            if (!doc.isObject())
              out = fail("not a JSON object: " + err.errorString());
            else {
              try {
                out = run(doc.object());
              } catch (const std::exception &e) {
                out = fail(QString("OpenToonz raised: ") + e.what());
              } catch (...) {
                out = fail("OpenToonz raised an exception");
              }
              if (doc.object().contains("id")) out["id"] = doc.object()["id"];
            }
            sock->write(QJsonDocument(out).toJson(QJsonDocument::Compact) + "\n");
            sock->flush();
          }
        });
      }
    });
    if (m_server.listen("otmcp-bridge"))
      logLine("listening on " + m_server.fullServerName());
    else
      logLine("could not listen: " + m_server.errorString());
  }
};

}  // namespace

// Qt may ask about an image from any thread, and before the application object exists. The bridge is started
// once, on the GUI thread, the first time both are possible.
void startBridgeOnce() {
  static std::atomic<bool> started{false};
  QCoreApplication *app = QCoreApplication::instance();
  if (!app || qEnvironmentVariable("OTBRIDGE") == "0") return;
  if (started.exchange(true)) return;
  QMetaObject::invokeMethod(
      app,
      [app]() {
        // This DLL calls C++ classes of OpenToonz directly, so it is only safe inside the exact build it was
        // compiled for. build.py recorded the digest of that build's libraries; anything else: stay off.
        QCryptographicHash h(QCryptographicHash::Sha256);
        for (const char *name : {OTBRIDGE_HOST_DLLS}) {
          QFile f(QCoreApplication::applicationDirPath() + "/" + name);
          if (!f.open(QIODevice::ReadOnly) || !h.addData(&f)) {
            logLine(QString("not started: cannot read %1").arg(name));
            return;
          }
        }
        if (h.result().toHex() != QByteArray(OTBRIDGE_HOST_DIGEST)) {
          logLine("not started: this OpenToonz is not the build the plugin was compiled for (" OTBRIDGE_HOST_LABEL
                  "). Rebuild it with bridge/build.py.");
          return;
        }
        new Bridge(app);
      },
      Qt::QueuedConnection);
}

class OtBridgePlugin final : public QImageIOPlugin {
  Q_OBJECT
  Q_PLUGIN_METADATA(IID "org.qt-project.Qt.QImageIOHandlerFactoryInterface" FILE "otbridge.json")
public:
  Capabilities capabilities(QIODevice *, const QByteArray &) const override {
    startBridgeOnce();
    return {};  // reads nothing, writes nothing: Qt moves on to its own PNG reader
  }
  QImageIOHandler *create(QIODevice *, const QByteArray &) const override { return nullptr; }
};

#include "otbridge.moc"
