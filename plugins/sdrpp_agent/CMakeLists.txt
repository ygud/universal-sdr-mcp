cmake_minimum_required(VERSION 3.13)
project(sdrpp_agent)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(SDRPP_CORE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../repos/SDRPlusPlus/core/src")
set(SDRPP_APP_FRAMEWORKS "/Applications/SDR++.app/Contents/Frameworks")

add_library(${PROJECT_NAME} SHARED
    src/main.cpp
)

target_include_directories(${PROJECT_NAME} PRIVATE
    ${SDRPP_CORE_ROOT}
    ${SDRPP_CORE_ROOT}/imgui
    ${CMAKE_CURRENT_SOURCE_DIR}/../../repos/SDRPlusPlus/decoder_modules/radio/src
    /opt/homebrew/include
)

target_link_directories(${PROJECT_NAME} PRIVATE
    ${SDRPP_APP_FRAMEWORKS}
    /opt/homebrew/lib
)

target_link_libraries(${PROJECT_NAME} PRIVATE
    sdrpp_core
)

target_compile_options(${PROJECT_NAME} PRIVATE
    -O3
    -Wno-nontrivial-memcall
    -Wno-deprecated-literal-operator
    -Wno-unused-command-line-argument
    -undefined dynamic_lookup
)

set_target_properties(${PROJECT_NAME} PROPERTIES
    PREFIX ""
    SUFFIX ".dylib"
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}"
)
