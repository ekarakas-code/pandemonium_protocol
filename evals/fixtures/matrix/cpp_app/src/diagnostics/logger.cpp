#include <string>

namespace engine {
namespace diagnostics {

// Central diagnostic sink for the engine. emit() records a failure message — for a failed
// collision resolution or a misbehaving system — so the frame's problems stay visible to the
// developer after the tick has finished.
struct Logger {
    void emit(const std::string& message) {
        lastMessage = message;
    }
    std::string lastMessage;
};

}  // namespace diagnostics
}  // namespace engine
