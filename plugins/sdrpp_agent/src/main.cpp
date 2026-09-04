#include <imgui.h>
#include <module.h>
#include <gui/gui.h>
#include <gui/style.h>
#include <gui/tuner.h>
#include <signal_path/signal_path.h>
#include <radio_interface.h>
#include <core.h>
#include <json.hpp>
#include <dsp/sink/handler_sink.h>
#include <dsp/types.h>

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <fcntl.h>

#include <string>
#include <vector>
#include <deque>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <atomic>
#include <chrono>
#include <fstream>
#include <cmath>
#include <algorithm>

using json = nlohmann::json;

#define DEFAULT_AGENT_PORT 19870
#define SAMPLE_OUTPUT_PATH "/tmp/sdr_sample.wav"

SDRPP_MOD_INFO{
    /* Name:            */ "sdrpp_agent",
    /* Description:     */ "AI Agent Co-pilot & MCP Integration Module",
    /* Author:          */ "Antigravity Research Team",
    /* Version:         */ 1, 0, 0,
    /* Max instances    */ 1
};

#pragma pack(push, 1)
struct WavHeader {
    char riff[4] = {'R', 'I', 'F', 'F'};
    uint32_t fileSize = 0;
    char wave[4] = {'W', 'A', 'V', 'E'};
    char fmt[4] = {'f', 'm', 't', ' '};
    uint32_t fmtSize = 16;
    uint16_t audioFormat = 1; // PCM
    uint16_t numChannels = 1; // Mono
    uint32_t sampleRate = 48000;
    uint32_t byteRate = 48000 * 2;
    uint16_t blockAlign = 2;
    uint16_t bitsPerSample = 16;
    char data[4] = {'d', 'a', 't', 'a'};
    uint32_t dataSize = 0;
};
#pragma pack(pop)

class AgentModule : public ModuleManager::Instance {
public:
    AgentModule(std::string name) {
        this->name = name;
        port = DEFAULT_AGENT_PORT;

        // Register GUI entry
        gui::menu.registerEntry(name, menuHandler, this, NULL);

        // Bind stream registration handlers
        streamRegHandler.ctx = this;
        streamRegHandler.handler = onStreamRegisteredHandler;
        streamUnregHandler.ctx = this;
        streamUnregHandler.handler = onStreamUnregisterHandler;
        sigpath::sinkManager.onStreamRegistered.bindHandler(&streamRegHandler);
        sigpath::sinkManager.onStreamUnregister.bindHandler(&streamUnregHandler);

        // Initial search for active audio stream
        auto streams = sigpath::sinkManager.getStreamNames();
        for (const auto& s : streams) {
            bindAudioStream(s);
            break;
        }

        // Start IPC server thread
        ipcRunning = true;
        ipcThread = std::thread(&AgentModule::ipcServerLoop, this);
    }

    ~AgentModule() {
        // Stop IPC
        ipcRunning = false;
        if (serverFd >= 0) {
            ::close(serverFd);
            serverFd = -1;
        }
        if (ipcThread.joinable()) {
            ipcThread.join();
        }

        // Unbind stream handlers
        sigpath::sinkManager.onStreamRegistered.unbindHandler(&streamRegHandler);
        sigpath::sinkManager.onStreamUnregister.unbindHandler(&streamUnregHandler);
        unbindAudioStream();

        gui::menu.removeEntry(name);
    }

    void postInit() {}

    void enable() { enabled = true; }
    void disable() { enabled = false; }
    bool isEnabled() { return enabled; }

private:
    std::string name;
    bool enabled = true;
    int port = DEFAULT_AGENT_PORT;

    // GUI state
    bool showFloatingConsole = true;
    std::atomic<bool> mcpConnected{false};
    std::string lastClientIp = "None";

    // Analysis results
    std::mutex analysisMtx;
    bool hasAnalysis = false;
    std::string analysisCountry = "";
    std::string analysisLanguage = "";
    std::string analysisDialect = "";
    std::string analysisStation = "";
    std::string analysisProgram = "";
    float analysisConfidence = 0.0f;
    std::vector<std::string> analysisEvidence;
    std::string lastSampleTime = "None";

    // Audio stream & recording
    std::string currentStreamName = "";
    dsp::stream<dsp::stereo_t>* audioStream = nullptr;
    dsp::sink::Handler<dsp::stereo_t> audioSink;
    std::mutex audioMtx;
    std::condition_variable sampleCv;
    bool isSampling = false;
    size_t targetSamples = 0;
    std::vector<int16_t> sampleBuffer;
    float audioSampleRate = 48000.0f;

    // Continuous recording
    bool isRecording = false;
    std::string recordingPath = "";
    std::ofstream recordingFile;
    size_t recordedSamples = 0;
    std::chrono::steady_clock::time_point recordingStartTime;

    // Stream handlers
    EventHandler<std::string> streamRegHandler;
    EventHandler<std::string> streamUnregHandler;

    // IPC Server
    std::atomic<bool> ipcRunning{false};
    int serverFd = -1;
    std::thread ipcThread;

    static void onStreamRegisteredHandler(std::string streamName, void* ctx) {
        AgentModule* _this = (AgentModule*)ctx;
        if (_this->audioStream == nullptr) {
            _this->bindAudioStream(streamName);
        }
    }

    static void onStreamUnregisterHandler(std::string streamName, void* ctx) {
        AgentModule* _this = (AgentModule*)ctx;
        if (_this->currentStreamName == streamName) {
            _this->unbindAudioStream();
        }
    }

    void bindAudioStream(const std::string& streamName) {
        std::lock_guard<std::mutex> lck(audioMtx);
        if (audioStream != nullptr) { return; }

        audioStream = sigpath::sinkManager.bindStream(streamName);
        if (audioStream) {
            currentStreamName = streamName;
            audioSampleRate = sigpath::sinkManager.getStreamSampleRate(streamName);
            if (audioSampleRate <= 0) { audioSampleRate = 48000.0f; }

            audioSink.init(audioStream, audioHandler, this);
            audioSink.start();
        }
    }

    void unbindAudioStream() {
        std::lock_guard<std::mutex> lck(audioMtx);
        if (audioStream) {
            audioSink.stop();
            sigpath::sinkManager.unbindStream(currentStreamName, audioStream);
            audioStream = nullptr;
            currentStreamName = "";
        }
    }

    static void audioHandler(dsp::stereo_t* data, int count, void* ctx) {
        AgentModule* _this = (AgentModule*)ctx;
        std::lock_guard<std::mutex> lck(_this->audioMtx);

        // Continuous recording
        if (_this->isRecording && _this->recordingFile.is_open()) {
            for (int i = 0; i < count; i++) {
                float mono = 0.5f * (data[i].l + data[i].r);
                int16_t val = (int16_t)std::clamp(mono * 32767.0f, -32768.0f, 32767.0f);
                _this->recordingFile.write(reinterpret_cast<const char*>(&val), sizeof(int16_t));
                _this->recordedSamples++;
            }
        }

        // Fixed-duration sampling
        if (_this->isSampling) {
            for (int i = 0; i < count; i++) {
                float mono = 0.5f * (data[i].l + data[i].r);
                int16_t val = (int16_t)std::clamp(mono * 32767.0f, -32768.0f, 32767.0f);
                _this->sampleBuffer.push_back(val);
                if (_this->sampleBuffer.size() >= _this->targetSamples) {
                    _this->isSampling = false;
                    _this->saveSampleWav();
                    _this->sampleCv.notify_all();
                    break;
                }
            }
        }
    }

    void saveSampleWav() {
        WavHeader hdr;
        hdr.sampleRate = (uint32_t)audioSampleRate;
        hdr.numChannels = 1;
        hdr.bitsPerSample = 16;
        hdr.byteRate = hdr.sampleRate * hdr.numChannels * (hdr.bitsPerSample / 8);
        hdr.blockAlign = hdr.numChannels * (hdr.bitsPerSample / 8);
        hdr.dataSize = (uint32_t)(sampleBuffer.size() * sizeof(int16_t));
        hdr.fileSize = sizeof(WavHeader) - 8 + hdr.dataSize;

        std::ofstream wavFile(SAMPLE_OUTPUT_PATH, std::ios::binary);
        if (wavFile.is_open()) {
            wavFile.write(reinterpret_cast<const char*>(&hdr), sizeof(WavHeader));
            wavFile.write(reinterpret_cast<const char*>(sampleBuffer.data()), hdr.dataSize);
            wavFile.close();

            auto now = std::chrono::system_clock::now();
            std::time_t t = std::chrono::system_clock::to_time_t(now);
            char timeBuf[64];
            std::strftime(timeBuf, sizeof(timeBuf), "%H:%M:%S", std::localtime(&t));
            lastSampleTime = std::string(timeBuf);
        }
    }

    // IPC Server Loop
    void ipcServerLoop() {
        serverFd = socket(AF_INET, SOCK_STREAM, 0);
        if (serverFd < 0) { return; }

        int opt = 1;
        setsockopt(serverFd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = inet_addr("127.0.0.1");
        addr.sin_port = htons(port);

        if (bind(serverFd, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
            ::close(serverFd);
            serverFd = -1;
            return;
        }

        if (listen(serverFd, 5) < 0) {
            ::close(serverFd);
            serverFd = -1;
            return;
        }

        while (ipcRunning) {
            sockaddr_in clientAddr{};
            socklen_t clientLen = sizeof(clientAddr);
            int clientFd = accept(serverFd, (struct sockaddr*)&clientAddr, &clientLen);
            if (clientFd < 0) {
                if (!ipcRunning) break;
                continue;
            }

            mcpConnected = true;
            lastClientIp = inet_ntoa(clientAddr.sin_addr);

            // Handle client connection (single client per connection or persistent)
            std::string buffer;
            char chunk[1024];
            while (ipcRunning) {
                ssize_t bytesRead = recv(clientFd, chunk, sizeof(chunk) - 1, 0);
                if (bytesRead <= 0) break;
                chunk[bytesRead] = '\0';
                buffer += chunk;

                size_t nlPos;
                while ((nlPos = buffer.find('\n')) != std::string::npos) {
                    std::string line = buffer.substr(0, nlPos);
                    buffer.erase(0, nlPos + 1);
                    if (!line.empty()) {
                        std::string resp = handleJsonRpc(line) + "\n";
                        send(clientFd, resp.c_str(), resp.size(), 0);
                    }
                }
            }

            ::close(clientFd);
            mcpConnected = false;
        }
    }

    std::string handleJsonRpc(const std::string& line) {
        try {
            json req = json::parse(line);
            std::string method = req.value("method", "");
            json reqId = nullptr;
            if (req.contains("id")) { reqId = req["id"]; }
            json params = req.contains("params") ? req["params"] : json::object();

            json resp;
            resp["jsonrpc"] = "2.0";
            resp["id"] = reqId;

            if (method == "sdr_status" || method == "sdr.get_status") {
                double freq = gui::waterfall.getCenterFrequency();
                std::string vfo = gui::waterfall.selectedVFO;
                if (!vfo.empty() && sigpath::vfoManager.vfoExists(vfo)) {
                    freq += sigpath::vfoManager.getOffset(vfo);
                }

                std::string modeStr = "UNKNOWN";
                if (!vfo.empty() && core::modComManager.getModuleName(vfo) == "radio") {
                    int mode = 0;
                    core::modComManager.callInterface(vfo, RADIO_IFACE_CMD_GET_MODE, NULL, &mode);
                    static const char* modes[] = {"NFM", "WFM", "AM", "DSB", "USB", "CW", "LSB", "RAW"};
                    if (mode >= 0 && mode <= 7) { modeStr = modes[mode]; }
                }

                bool hasAudio = (audioStream != nullptr);
                resp["result"] = {
                    {"connected", true},
                    {"frequency", freq},
                    {"frequency_khz", freq / 1000.0},
                    {"vfo", vfo},
                    {"mode", modeStr},
                    {"audio_ready", hasAudio},
                    {"audio_stream", currentStreamName},
                    {"sample_rate", audioSampleRate},
                    {"version", "1.0.0"}
                };
            }
            else if (method == "sdr_tune" || method == "sdr.tune") {
                double freq = params.value("frequency", 0.0);
                std::string mode = params.value("mode", "");

                std::string vfo = gui::waterfall.selectedVFO;
                if (vfo.empty() && !gui::waterfall.vfos.empty()) {
                    vfo = gui::waterfall.vfos.begin()->first;
                }

                if (!vfo.empty() && freq > 0) {
                    tuner::normalTuning(vfo, freq);
                }

                if (!mode.empty() && !vfo.empty() && core::modComManager.getModuleName(vfo) == "radio") {
                    int m = -1;
                    if (mode == "NFM") m = 0;
                    else if (mode == "WFM") m = 1;
                    else if (mode == "AM") m = 2;
                    else if (mode == "DSB") m = 3;
                    else if (mode == "USB") m = 4;
                    else if (mode == "CW") m = 5;
                    else if (mode == "LSB") m = 6;
                    else if (mode == "RAW") m = 7;
                    if (m >= 0) {
                        core::modComManager.callInterface(vfo, RADIO_IFACE_CMD_SET_MODE, &m, NULL);
                    }
                }

                resp["result"] = {
                    {"success", true},
                    {"frequency", freq},
                    {"mode", mode}
                };
            }
            else if (method == "sdr_sample_audio" || method == "sdr.sample_audio") {
                float dur = params.value("duration_sec", 10.0f);
                if (dur <= 0.1f) dur = 1.0f;
                if (dur > 60.0f) dur = 60.0f;

                // Ensure SDR playback is active so demodulator produces audio
                if (!gui::mainWindow.isPlaying()) {
                    gui::mainWindow.setPlayState(true);
                    std::this_thread::sleep_for(std::chrono::milliseconds(300));
                }

                // Ensure stream is bound
                if (!audioStream) {
                    auto streams = sigpath::sinkManager.getStreamNames();
                    for (const auto& s : streams) {
                        bindAudioStream(s);
                        break;
                    }
                }

                bool sampleOk = false;
                if (audioStream) {
                    {
                        std::lock_guard<std::mutex> lck(audioMtx);
                        sampleBuffer.clear();
                        targetSamples = (size_t)(dur * audioSampleRate);
                        isSampling = true;
                    }

                    // Wait for sampling to complete with timeout
                    std::unique_lock<std::mutex> lck(audioMtx);
                    sampleOk = sampleCv.wait_for(lck, std::chrono::milliseconds((int)(dur * 1000 + 3000)), [this]() {
                        return !isSampling;
                    });
                    isSampling = false;
                }

                resp["result"] = {
                    {"success", sampleOk},
                    {"path", SAMPLE_OUTPUT_PATH},
                    {"duration_sec", dur},
                    {"sample_rate", (int)audioSampleRate},
                    {"channels", 1},
                    {"samples_recorded", (int)sampleBuffer.size()}
                };
            }
            else if (method == "sdr_devices" || method == "sdr.devices") {
                auto sources = sigpath::sourceManager.getSourceNames();
                resp["result"] = {
                    {"available_sources", sources},
                    {"count", (int)sources.size()}
                };
            }
            else if (method == "sdr_get_spectrum" || method == "sdr.get_spectrum") {
                int width = 0;
                float* fftData = gui::waterfall.acquireLatestFFT(width);
                if (!fftData || width <= 0) {
                    gui::waterfall.releaseLatestFFT();
                    resp["result"] = {
                        {"available", false},
                        {"error", "FFT spectrum not currently available (ensure waterfall is rendering)"}
                    };
                } else {
                    double centerFreq = gui::waterfall.getCenterFrequency();
                    double bandwidth = gui::waterfall.getBandwidth();
                    float minDb = gui::waterfall.getFFTMin();
                    float maxDb = gui::waterfall.getFFTMax();

                    // Downsample FFT bins to standard 256 points for compact network transport
                    int targetBins = 256;
                    int step = std::max(1, width / targetBins);
                    std::vector<float> bins;
                    bins.reserve(targetBins);
                    float peakVal = -999.0f;
                    int peakIdx = 0;
                    float sumDb = 0.0f;

                    for (int i = 0; i < width && (int)bins.size() < targetBins; i += step) {
                        float val = fftData[i];
                        float rounded = std::round(val * 10.0f) / 10.0f;
                        bins.push_back(rounded);
                        sumDb += rounded;
                        if (val > peakVal) {
                            peakVal = rounded;
                            peakIdx = (int)bins.size() - 1;
                        }
                    }
                    gui::waterfall.releaseLatestFFT();

                    double peakFreq = (centerFreq - bandwidth / 2.0) + ((double)peakIdx / (double)bins.size()) * bandwidth;
                    float avgDb = bins.empty() ? 0.0f : (sumDb / bins.size());

                    resp["result"] = {
                        {"available", true},
                        {"center_frequency", centerFreq},
                        {"bandwidth", bandwidth},
                        {"start_frequency", centerFreq - bandwidth / 2.0},
                        {"end_frequency", centerFreq + bandwidth / 2.0},
                        {"min_db", minDb},
                        {"max_db", maxDb},
                        {"peak_db", peakVal},
                        {"peak_frequency", peakFreq},
                        {"avg_db", avgDb},
                        {"bin_count", (int)bins.size()},
                        {"bins", bins}
                    };
                }
            }
            else if (method == "sdr_set_sample_rate" || method == "sdr.set_sample_rate") {
                double sr = params.value("sample_rate", 0.0);
                if (sr > 0) {
                    core::setInputSampleRate(sr);
                    resp["result"] = {
                        {"success", true},
                        {"sample_rate", sr}
                    };
                } else {
                    resp["error"] = {{"code", -32602}, {"message", "Invalid sample_rate parameter"}};
                }
            }
            else if (method == "sdr_set_gain" || method == "sdr.set_gain") {
                resp["result"] = {
                    {"supported", false},
                    {"status", "NOT_SUPPORTED"},
                    {"message", "Hardware gain control is managed by SDR++ Source panel in this backend"}
                };
            }
            else if (method == "sdr_start_recording" || method == "sdr.start_recording") {
                std::string path = params.value("path", "");
                if (path.empty()) {
                    path = "/tmp/sdr_recording_" + std::to_string(std::time(nullptr)) + ".wav";
                }

                if (!gui::mainWindow.isPlaying()) {
                    gui::mainWindow.setPlayState(true);
                    std::this_thread::sleep_for(std::chrono::milliseconds(200));
                }

                if (!audioStream) {
                    auto streams = sigpath::sinkManager.getStreamNames();
                    for (const auto& s : streams) {
                        bindAudioStream(s);
                        break;
                    }
                }

                std::lock_guard<std::mutex> lck(audioMtx);
                if (isRecording) {
                    resp["error"] = {{"code", -32001}, {"message", "Recording already in progress: " + recordingPath}};
                } else {
                    recordingFile.open(path, std::ios::binary);
                    if (!recordingFile.is_open()) {
                        resp["error"] = {{"code", -32002}, {"message", "Failed to open file for recording: " + path}};
                    } else {
                        WavHeader dummyHdr;
                        dummyHdr.sampleRate = (uint32_t)audioSampleRate;
                        dummyHdr.byteRate = dummyHdr.sampleRate * 2;
                        recordingFile.write(reinterpret_cast<const char*>(&dummyHdr), sizeof(WavHeader));
                        isRecording = true;
                        recordingPath = path;
                        recordedSamples = 0;
                        recordingStartTime = std::chrono::steady_clock::now();
                        resp["result"] = {
                            {"status", "recording_started"},
                            {"path", path},
                            {"sample_rate", (int)audioSampleRate},
                            {"channels", 1}
                        };
                    }
                }
            }
            else if (method == "sdr_stop_recording" || method == "sdr.stop_recording") {
                std::lock_guard<std::mutex> lck(audioMtx);
                if (!isRecording) {
                    resp["error"] = {{"code", -32003}, {"message", "No active recording in progress"}};
                } else {
                    isRecording = false;
                    auto now = std::chrono::steady_clock::now();
                    double duration = std::chrono::duration<double>(now - recordingStartTime).count();

                    WavHeader finalHdr;
                    finalHdr.sampleRate = (uint32_t)audioSampleRate;
                    finalHdr.byteRate = finalHdr.sampleRate * 2;
                    finalHdr.dataSize = (uint32_t)(recordedSamples * sizeof(int16_t));
                    finalHdr.fileSize = sizeof(WavHeader) - 8 + finalHdr.dataSize;

                    recordingFile.seekp(0, std::ios::beg);
                    recordingFile.write(reinterpret_cast<const char*>(&finalHdr), sizeof(WavHeader));
                    recordingFile.close();

                    resp["result"] = {
                        {"status", "recording_stopped"},
                        {"path", recordingPath},
                        {"duration_sec", duration},
                        {"sample_rate", (int)audioSampleRate},
                        {"samples_recorded", (int)recordedSamples},
                        {"size_bytes", (int)(sizeof(WavHeader) + recordedSamples * sizeof(int16_t))}
                    };
                    recordingPath = "";
                    recordedSamples = 0;
                }
            }
            else if (method == "sdr_update_analysis" || method == "sdr.update_analysis") {
                std::lock_guard<std::mutex> lck(analysisMtx);
                hasAnalysis = true;
                analysisCountry = params.value("country", "");
                analysisLanguage = params.value("language", "");
                analysisDialect = params.value("dialect", "");
                analysisStation = params.value("station", "");
                analysisProgram = params.value("program", "");
                analysisConfidence = params.value("confidence", 0.0f);

                analysisEvidence.clear();
                if (params.contains("evidence") && params["evidence"].is_array()) {
                    for (const auto& item : params["evidence"]) {
                        analysisEvidence.push_back(item.get<std::string>());
                    }
                }

                resp["result"] = {{"success", true}};
            }
            else if (method == "sdr_health") {
                resp["result"] = {
                    {"status", "ok"},
                    {"version", "1.0.0"},
                    {"port", port}
                };
            }
            else {
                resp["error"] = {{"code", -32601}, {"message", "Method not found: " + method}};
            }

            return resp.dump();
        }
        catch (const std::exception& e) {
            json err;
            err["jsonrpc"] = "2.0";
            err["id"] = nullptr;
            err["error"] = {{"code", -32700}, {"message", std::string("Parse error: ") + e.what()}};
            return err.dump();
        }
    }

    static void drawConsoleWidgets(AgentModule* _this) {
        float menuWidth = ImGui::GetContentRegionAvail().x;

        ImGui::TextColored(ImVec4(0.2f, 1.0f, 0.4f, 1.0f), "[SDR Agent]");
        ImGui::SameLine();
        if (_this->mcpConnected) {
            ImGui::TextColored(ImVec4(0.2f, 1.0f, 0.2f, 1.0f), "● MCP Connected");
        } else {
            ImGui::TextColored(ImVec4(0.7f, 0.7f, 0.7f, 1.0f), "○ Listening :%d", _this->port);
        }

        ImGui::Separator();

        // Current Hardware Status
        double curFreq = gui::waterfall.getCenterFrequency();
        std::string vfo = gui::waterfall.selectedVFO;
        if (!vfo.empty() && sigpath::vfoManager.vfoExists(vfo)) {
            curFreq += sigpath::vfoManager.getOffset(vfo);
        }

        std::string curMode = "UNKNOWN";
        if (!vfo.empty() && core::modComManager.getModuleName(vfo) == "radio") {
            int mode = 0;
            core::modComManager.callInterface(vfo, RADIO_IFACE_CMD_GET_MODE, NULL, &mode);
            static const char* modes[] = {"NFM", "WFM", "AM", "DSB", "USB", "CW", "LSB", "RAW"};
            if (mode >= 0 && mode <= 7) { curMode = modes[mode]; }
        }

        ImGui::Text("Frequency: %.1f kHz", curFreq / 1000.0);
        ImGui::Text("Mode: %s  (VFO: %s)", curMode.c_str(), vfo.empty() ? "None" : vfo.c_str());

        if (_this->audioStream) {
            ImGui::TextColored(ImVec4(0.2f, 0.9f, 0.2f, 1.0f), "Audio: Ready (%s, %.0f Hz)",
                               _this->currentStreamName.c_str(), _this->audioSampleRate);
        } else {
            ImGui::TextColored(ImVec4(0.9f, 0.6f, 0.2f, 1.0f), "Audio: Waiting for stream...");
            if (ImGui::Button("Attach Radio Stream", ImVec2(menuWidth, 0))) {
                auto streams = sigpath::sinkManager.getStreamNames();
                for (const auto& s : streams) {
                    _this->bindAudioStream(s);
                    break;
                }
            }
        }

        ImGui::Separator();

        // Latest Sample Info
        if (_this->isSampling) {
            ImGui::TextColored(ImVec4(1.0f, 0.2f, 0.2f, 1.0f), "● RECORDING AUDIO (10s)...");
        } else {
            ImGui::Text("Last Sample: %s", _this->lastSampleTime.c_str());
        }

        ImGui::Separator();

        // Analysis Result Display
        {
            std::lock_guard<std::mutex> lck(_this->analysisMtx);
            if (_this->hasAnalysis) {
                ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.2f, 1.0f), "=== Multimodal Analysis ===");
                ImGui::Text("Country:    %s", _this->analysisCountry.c_str());
                ImGui::Text("Language:   %s", _this->analysisLanguage.c_str());
                if (!_this->analysisDialect.empty()) {
                    ImGui::Text("Dialect:    %s", _this->analysisDialect.c_str());
                }
                ImGui::Text("Station:    %s", _this->analysisStation.c_str());
                ImGui::Text("Program:    %s", _this->analysisProgram.c_str());

                // Confidence Bar
                ImVec4 confColor = ImVec4(1.0f, 0.2f, 0.2f, 1.0f);
                if (_this->analysisConfidence >= 0.8f) confColor = ImVec4(0.2f, 1.0f, 0.3f, 1.0f);
                else if (_this->analysisConfidence >= 0.5f) confColor = ImVec4(1.0f, 0.8f, 0.2f, 1.0f);

                ImGui::TextColored(confColor, "Confidence: %.0f%%", _this->analysisConfidence * 100.0f);

                if (!_this->analysisEvidence.empty()) {
                    ImGui::TextUnformatted("Evidence:");
                    for (const auto& ev : _this->analysisEvidence) {
                        ImGui::BulletText("%s", ev.c_str());
                    }
                }
            } else {
                ImGui::TextDisabled("No AI analysis yet.");
                ImGui::TextDisabled("Use AntiGravity MCP or tune & sample.");
            }
        }
    }

    // GUI Menu Handler
    static void menuHandler(void* ctx) {
        AgentModule* _this = (AgentModule*)ctx;
        drawConsoleWidgets(_this);

        ImGui::Separator();
        ImGui::Checkbox("Floating Console Window", &_this->showFloatingConsole);

        if (_this->showFloatingConsole) {
            ImGui::SetNextWindowSize(ImVec2(380, 560), ImGuiCond_FirstUseEver);
            ImGui::SetNextWindowPos(ImVec2(860, 90), ImGuiCond_FirstUseEver);
            if (ImGui::Begin("SDR++ AI Agent Console", &_this->showFloatingConsole)) {
                drawConsoleWidgets(_this);
            }
            ImGui::End();
        }
    }
};

MOD_EXPORT void _INIT_() {
    // Plugin initialization
}

MOD_EXPORT ModuleManager::Instance* _CREATE_INSTANCE_(std::string name) {
    return new AgentModule(name);
}

MOD_EXPORT void _DELETE_INSTANCE_(void* instance) {
    delete (AgentModule*)instance;
}

MOD_EXPORT void _END_() {
    // Plugin cleanup
}
