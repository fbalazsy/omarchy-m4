// SPDX-License-Identifier: MIT
// Copyright (c) 2026 KB2UKA (original measure-memory.c / measure-transfer.c, Omarchy-M4-Native @ b92f048)
// J616 variant (lane SF, macOS-side helper, 2026-10-21): the same in-memory offline render as
// Kb2uka's helpers (RegisterAudioUnits_Internal, AudioUnitRender with a render callback and
// manual timestamps; no output unit, no audio device), generalised so that
//   - the AU type/subtype and the channel count (2 or 6) are arguments,
//   - arbitrary global-scope parameters can be set after AudioUnitInitialize (ID=VALUE),
//   - the preset is applied exactly as stored (kAudioUnitProperty_ClassInfo), never edited,
//   - the input is either one impulse (lane, amplitude) or a float32 file (CHANNELS*FRAMES, planar).
// Render loop, block size, frame count, impulse position and the stream format are Kb2uka's.
#include <AudioToolbox/AudioToolbox.h>
#include <CoreFoundation/CoreFoundation.h>
#include <dlfcn.h>
#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXCH 6
#define BLOCK 256
#define FRAMES 73728
#define IMPULSE 4096  /* default impulse frame (Kb2uka's value); "amplitude@frame" overrides it */
#define MAXPARAM 64

struct input {
    unsigned channels;
    unsigned lane;
    float amplitude;
    long impulse_frame;
    unsigned calls;
    unsigned impulses;
    const float *file_samples;
    float samples[MAXCH][BLOCK];
};

static OSStatus supply(void *ref, AudioUnitRenderActionFlags *flags,
                       const AudioTimeStamp *stamp, UInt32 bus,
                       UInt32 frames, AudioBufferList *buffers) {
    (void)flags;
    struct input *input = ref;
    if (bus != 0 || frames > BLOCK || buffers->mNumberBuffers != input->channels ||
        !(stamp->mFlags & kAudioTimeStampSampleTimeValid) ||
        !isfinite(stamp->mSampleTime) || stamp->mSampleTime != floor(stamp->mSampleTime))
        return kAudio_ParamError;
    input->calls++;
    for (unsigned ch = 0; ch < input->channels; ch++) {
        if (buffers->mBuffers[ch].mNumberChannels != 1) return kAudio_ParamError;
        buffers->mBuffers[ch].mData = input->samples[ch];
        buffers->mBuffers[ch].mDataByteSize = frames * sizeof(float);
        memset(input->samples[ch], 0, frames * sizeof(float));
    }
    if (input->file_samples) {
        long start = (long)stamp->mSampleTime;
        if (start < 0 || start + frames > FRAMES) return kAudio_ParamError;
        for (unsigned ch = 0; ch < input->channels; ch++)
            memcpy(input->samples[ch], input->file_samples + (size_t)ch * FRAMES + start, frames * sizeof(float));
        return noErr;
    }
    long offset = input->impulse_frame - (long)stamp->mSampleTime;
    if (offset >= 0 && offset < frames) {
        input->samples[input->lane][offset] = input->amplitude;
        input->impulses++;
    }
    return noErr;
}

static int fail(const char *label, OSStatus status) {
    fprintf(stderr, "%s status=%d\n", label, (int)status);
    return 1;
}

static unsigned fourcc(const char *s) {
    return ((unsigned)(unsigned char)s[0] << 24) | ((unsigned)(unsigned char)s[1] << 16) |
           ((unsigned)(unsigned char)s[2] << 8) | (unsigned)(unsigned char)s[3];
}

int main(int argc, char **argv) {
    if (argc < 9) return fail("usage: type subtype preset output channels lane amplitude input-or-impulse [ID=VALUE...]", -1);
    if (strlen(argv[1]) != 4 || strlen(argv[2]) != 4) return 2;
    char *end;
    unsigned channels = (unsigned)strtoul(argv[5], &end, 10);
    if (*end || channels < 1 || channels > MAXCH) return 2;
    unsigned lane = (unsigned)strtoul(argv[6], &end, 10);
    if (*end || lane >= channels) return 2;
    float amplitude = strtof(argv[7], &end);
    long impulse_frame = IMPULSE;
    if (*end == '@') {
        impulse_frame = strtol(end + 1, &end, 10);
        if (impulse_frame < IMPULSE || impulse_frame >= FRAMES - BLOCK) return 3;
    }
    if (*end || !isfinite(amplitude) || amplitude <= 0 || amplitude > 1.0f) return 3;
    unsigned nparam = (unsigned)(argc - 9);
    if (nparam > MAXPARAM) return 4;
    unsigned pid[MAXPARAM]; float pval[MAXPARAM];
    for (unsigned i = 0; i < nparam; i++) {
        pid[i] = (unsigned)strtoul(argv[9 + i], &end, 10);
        if (*end != '=') return 4;
        pval[i] = strtof(end + 1, &end);
        if (*end || !isfinite(pval[i])) return 4;
    }
    void *handle = dlopen("/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP", RTLD_NOW | RTLD_LOCAL);
    if (!handle) return fail("dlopen", -1);
    void (*reg)(void) = dlsym(handle, "RegisterAudioUnits_Internal");
    if (!reg) return fail("registration", -1);
    reg();
    AudioComponentDescription d = { .componentType = fourcc(argv[1]), .componentSubType = fourcc(argv[2]), .componentManufacturer = 'appl' };
    AudioComponent c = AudioComponentFindNext(NULL, &d);
    AudioComponentInstance au = NULL;
    if (!c) return fail("component", -1);
    OSStatus status = AudioComponentInstanceNew(c, &au);
    if (status || !au) return fail("instance", status);
    FILE *file = fopen(argv[3], "rb");
    if (!file) return fail("preset open", -1);
    if (fseek(file, 0, SEEK_END)) return 5;
    long count = ftell(file);
    if (count <= 0 || count > 1000000 || fseek(file, 0, SEEK_SET)) return 6;
    unsigned char *raw = malloc((size_t)count);
    if (!raw || fread(raw, 1, (size_t)count, file) != (size_t)count) return 7;
    fclose(file);
    CFDataRef data = CFDataCreate(NULL, raw, count);
    free(raw);
    CFPropertyListRef preset = CFPropertyListCreateWithData(NULL, data, kCFPropertyListImmutable, NULL, NULL);
    CFRelease(data);
    if (!preset || CFGetTypeID(preset) != CFDictionaryGetTypeID()) return 8;
    status = AudioUnitSetProperty(au, kAudioUnitProperty_ClassInfo, kAudioUnitScope_Global, 0, &preset, sizeof(preset));
    CFRelease(preset);
    if (status) return fail("preset load", status);
    AudioStreamBasicDescription format = {
        .mSampleRate = 48000, .mFormatID = kAudioFormatLinearPCM,
        .mFormatFlags = kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked | kAudioFormatFlagIsNonInterleaved,
        .mBytesPerPacket = sizeof(float), .mFramesPerPacket = 1,
        .mBytesPerFrame = sizeof(float), .mChannelsPerFrame = channels, .mBitsPerChannel = 32
    };
    for (unsigned scope = kAudioUnitScope_Input; scope <= kAudioUnitScope_Output; scope++) {
        status = AudioUnitSetProperty(au, kAudioUnitProperty_StreamFormat, scope, 0, &format, sizeof(format));
        if (status) return fail("stream format", status);
        AudioStreamBasicDescription actual = {0}; UInt32 size = sizeof(actual);
        status = AudioUnitGetProperty(au, kAudioUnitProperty_StreamFormat, scope, 0, &actual, &size);
        if (status || memcmp(&format, &actual, sizeof(format))) return fail("format readback", status);
    }
    UInt32 maxframes = BLOCK;
    status = AudioUnitSetProperty(au, kAudioUnitProperty_MaximumFramesPerSlice, kAudioUnitScope_Global, 0, &maxframes, sizeof(maxframes));
    if (status) return fail("maximum frames", status);
    struct input input = { .channels = channels, .lane = lane, .amplitude = amplitude, .impulse_frame = impulse_frame };
    float *file_input = NULL;
    if (strcmp(argv[8], "impulse")) {
        file = fopen(argv[8], "rb");
        if (!file) return fail("input open", -1);
        file_input = malloc((size_t)channels * FRAMES * sizeof(float));
        if (!file_input || fread(file_input, sizeof(float), (size_t)channels * FRAMES, file) != (size_t)channels * FRAMES || fgetc(file) != EOF)
            return fail("input length", -1);
        fclose(file);
        for (unsigned i = 0; i < channels * FRAMES; i++)
            if (!isfinite(file_input[i]) || fabsf(file_input[i]) > 1.0f) return fail("input bound", -1);
        input.file_samples = file_input;
    }
    AURenderCallbackStruct cb = { .inputProc = supply, .inputProcRefCon = &input };
    status = AudioUnitSetProperty(au, kAudioUnitProperty_SetRenderCallback, kAudioUnitScope_Input, 0, &cb, sizeof(cb));
    if (status) return fail("memory callback", status);
    status = AudioUnitInitialize(au);
    if (status) return fail("initialize effect", status);
    float readback[MAXPARAM];
    for (unsigned i = 0; i < nparam; i++) {
        status = AudioUnitSetParameter(au, pid[i], kAudioUnitScope_Global, 0, pval[i], 0);
        if (status) return fail("set parameter", status);
        AudioUnitParameterValue actual = -12345;
        status = AudioUnitGetParameter(au, pid[i], kAudioUnitScope_Global, 0, &actual);
        if (status) return fail("get parameter", status);
        readback[i] = actual;
        if (fabsf(actual - pval[i]) > 1e-6f * fmaxf(1.0f, fabsf(pval[i]))) {
            fprintf(stderr, "parameter %u wanted %.9g got %.9g\n", pid[i], pval[i], actual);
            return 12;
        }
    }
    Float64 latency = -1, tail = -1; UInt32 size = sizeof(latency);
    OSStatus latency_status = AudioUnitGetProperty(au, kAudioUnitProperty_Latency, kAudioUnitScope_Global, 0, &latency, &size);
    size = sizeof(tail);
    OSStatus tail_status = AudioUnitGetProperty(au, kAudioUnitProperty_TailTime, kAudioUnitScope_Global, 0, &tail, &size);
    AudioBufferList *buffers = calloc(1, offsetof(AudioBufferList, mBuffers) + channels * sizeof(AudioBuffer));
    float *response = calloc((size_t)channels * FRAMES, sizeof(float));
    float block[MAXCH][BLOCK];
    if (!buffers || !response) return 9;
    buffers->mNumberBuffers = channels;
    for (unsigned frame = 0; frame < FRAMES; frame += BLOCK) {
        for (unsigned ch = 0; ch < channels; ch++) {
            buffers->mBuffers[ch].mNumberChannels = 1;
            buffers->mBuffers[ch].mDataByteSize = BLOCK * sizeof(float);
            buffers->mBuffers[ch].mData = block[ch];
            memset(block[ch], 0, sizeof(block[ch]));
        }
        AudioTimeStamp stamp = { .mSampleTime = frame, .mFlags = kAudioTimeStampSampleTimeValid };
        AudioUnitRenderActionFlags flags = 0;
        status = AudioUnitRender(au, &flags, &stamp, 0, BLOCK, buffers);
        if (status) return fail("memory render", status);
        for (unsigned ch = 0; ch < channels; ch++) {
            if (buffers->mBuffers[ch].mDataByteSize != BLOCK * sizeof(float) || !buffers->mBuffers[ch].mData) return 10;
            const float *samples = buffers->mBuffers[ch].mData;
            for (unsigned i = 0; i < BLOCK; i++) {
                if (!isfinite(samples[i])) return fail("nonfinite output", -1);
                response[(size_t)ch * FRAMES + frame + i] = samples[i];
            }
        }
    }
    if (input.impulses != (file_input ? 0u : 1u)) return fail("input impulse count", (OSStatus)input.impulses);
    status = AudioUnitUninitialize(au);
    if (status) return fail("uninitialize", status);
    status = AudioComponentInstanceDispose(au);
    if (status) return fail("dispose", status);
    file = fopen(argv[4], "wb");
    if (!file || fwrite(response, sizeof(float), (size_t)channels * FRAMES, file) != (size_t)channels * FRAMES || fclose(file)) return 11;
    printf("{\"type\":\"%s\",\"subtype\":\"%s\",\"sample_rate\":48000,\"channels\":%u,\"frames\":%u,\"impulse_frame\":%u,\"lane\":%u,\"amplitude\":%.9g,\"callbacks\":%u,\"impulses\":%u,\"latency_status\":%d,\"latency\":%.9g,\"tail_status\":%d,\"tail\":%.9g,\"params\":{",
           argv[1], argv[2], channels, FRAMES, (unsigned)impulse_frame, lane, amplitude, input.calls, input.impulses,
           (int)latency_status, latency, (int)tail_status, tail);
    for (unsigned i = 0; i < nparam; i++) printf("%s\"%u\":%.9g", i ? "," : "", pid[i], readback[i]);
    printf("}}\n");
    free(response); free(buffers); free(file_input);
    return 0;
}
