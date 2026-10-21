/* ARM64 Linux syscalls must preserve all vector state across scheduling.
 * No C/runtime function executes while the tested Q/FP registers are live.
 * x0=input vectors, x1=output vectors, x2=affinity mask, x3=FP control/result.
 * Native test userspace only: no implementation-defined or privileged sysreg. */
.text
.global cpu_context_round
.type cpu_context_round, %function
cpu_context_round:
    sub sp, sp, #80
    stp d8, d9, [sp, #0]
    stp d10, d11, [sp, #16]
    stp d12, d13, [sp, #32]
    stp d14, d15, [sp, #48]
    mrs x4, fpcr
    mrs x5, fpsr
    stp x4, x5, [sp, #64]
    mov x9, x0
    mov x10, x1
    mov x11, x2
    mov x12, x3
    ldp x4, x5, [x12]
    msr fpcr, x4
    msr fpsr, x5
    ldp q0, q1, [x9, #0]
    ldp q2, q3, [x9, #32]
    ldp q4, q5, [x9, #64]
    ldp q6, q7, [x9, #96]
    ldp q8, q9, [x9, #128]
    ldp q10, q11, [x9, #160]
    ldp q12, q13, [x9, #192]
    ldp q14, q15, [x9, #224]
    ldp q16, q17, [x9, #256]
    ldp q18, q19, [x9, #288]
    ldp q20, q21, [x9, #320]
    ldp q22, q23, [x9, #352]
    ldp q24, q25, [x9, #384]
    ldp q26, q27, [x9, #416]
    ldp q28, q29, [x9, #448]
    ldp q30, q31, [x9, #480]
    mov x0, #0                      // sched_setaffinity(0, 8, mask)
    mov x1, #8
    mov x2, x11
    mov x8, #122
    svc #0
    cbnz x0, 1f
    mov x8, #124                    // sched_yield
    svc #0
1:
    stp q0, q1, [x10, #0]
    stp q2, q3, [x10, #32]
    stp q4, q5, [x10, #64]
    stp q6, q7, [x10, #96]
    stp q8, q9, [x10, #128]
    stp q10, q11, [x10, #160]
    stp q12, q13, [x10, #192]
    stp q14, q15, [x10, #224]
    stp q16, q17, [x10, #256]
    stp q18, q19, [x10, #288]
    stp q20, q21, [x10, #320]
    stp q22, q23, [x10, #352]
    stp q24, q25, [x10, #384]
    stp q26, q27, [x10, #416]
    stp q28, q29, [x10, #448]
    stp q30, q31, [x10, #480]
    mrs x4, fpcr
    mrs x5, fpsr
    stp x4, x5, [x12, #16]
    ldp x4, x5, [sp, #64]
    msr fpcr, x4
    msr fpsr, x5
    ldp d8, d9, [sp, #0]
    ldp d10, d11, [sp, #16]
    ldp d12, d13, [sp, #32]
    ldp d14, d15, [sp, #48]
    add sp, sp, #80
    ret
.size cpu_context_round, .-cpu_context_round
.section .note.GNU-stack,"",%progbits
