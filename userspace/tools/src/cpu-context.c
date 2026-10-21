/* Bounded RAM-only CPU cohort SIMD/scheduler admission. Positive child reaping
 * is required before returning safe_to_continue. No devices or disk writes. */
#define _GNU_SOURCE
#include <errno.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum { ROUNDS = 256, MAX_CPUS = 14 };
static volatile sig_atomic_t interrupted;
static void interrupt(int number) { interrupted = number; }
extern long cpu_context_round(const unsigned char *, unsigned char *, const uint64_t *, uint64_t *);
struct report { unsigned worker, rounds, visits[MAX_CPUS], mismatch, error; };
static double now(void)
{ struct timespec t; if(clock_gettime(CLOCK_MONOTONIC,&t))return 0;return t.tv_sec+t.tv_nsec/1e9; }
static void child(int fd, int start, unsigned worker, const int cpu[MAX_CPUS], unsigned count, int inject)
{
    unsigned char before[512] __attribute__((aligned(16))), after[512];
    struct report r={.worker=worker}; char ready;
    signal(SIGTERM, SIG_DFL); signal(SIGINT, SIG_DFL);
    alarm(5);
    if(read(start,&ready,1)!=1){r.error=1;goto done;}
    close(start);
    for(unsigned round=0;round<ROUNDS;round++) {
        unsigned target=(round+worker)%count;
        uint64_t mask=UINT64_C(1)<<cpu[target];
        uint64_t fp[4]={(uint64_t)(worker+1)<<22,worker?1:0x10,0,0};
        for(unsigned i=0;i<sizeof(before);i++)before[i]=(unsigned char)(i*73+round*29+worker*131);
        memset(after,0,sizeof(after));
        if(cpu_context_round(before,after,&mask,fp)||sched_getcpu()!=cpu[target]){r.error=2;break;}
        if(inject&&worker==0&&round==0)after[511]^=1;
        if(memcmp(before,after,sizeof(before))||fp[0]!=fp[2]||fp[1]!=fp[3]){r.mismatch=1;break;}
        r.visits[target]++;r.rounds++;
    }
done:
    /* One <= PIPE_BUF result; parent accepts complete bytes and positive exit. */
    if(write(fd,&r,sizeof(r))!=(ssize_t)sizeof(r))_exit(3);
    close(fd);_exit(r.rounds==ROUNDS&&!r.error&&!r.mismatch?0:1);
}
int main(int argc,char **argv)
{
    int self=argc==2&&!strcmp(argv[1],"--self-check");
    int inject=argc==2&&!strcmp(argv[1],"--inject-bad");
    if(argc>2||(argc==2&&!self&&!inject))return 2;
    cpu_set_t original;
    if(sched_getaffinity(0,sizeof(original),&original))return 2;
    int cpu[MAX_CPUS];unsigned count=0;
    for(unsigned n=0;n<MAX_CPUS;n++)cpu[n]=-1;
    for(int n=0;n<64;n++)if(CPU_ISSET(n,&original)){
        if(count<MAX_CPUS)cpu[count]=n;
        count++;
    }
    if(count<2||count>MAX_CPUS||(!self&&!inject&&
        (sysconf(_SC_PAGESIZE)!=16384||cpu[0]!=0||cpu[count-1]!=(int)count-1))) {
        puts("{\"status\":\"REFUSED_BEFORE_CPU_CONTEXT_TEST\",\"passed\":false,\"safe_to_continue\":true}");return 1;
    }
    struct sigaction action={.sa_handler=interrupt};
    sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,NULL)||sigaction(SIGINT,&action,NULL))return 2;
    int data[2],start[2];
    signal(SIGPIPE,SIG_IGN);
    if(pipe(data)||pipe(start))return 2;
    pid_t pid[MAX_CPUS]={0};int status[MAX_CPUS],safe=1,failed=0;
    for(unsigned n=0;n<MAX_CPUS;n++)status[n]=-1;
    double began=now(),deadline=began+6;
    for(unsigned worker=0;worker<count;worker++) {
        pid[worker]=fork();
        if(pid[worker]<0){failed=1;break;}
        if(!pid[worker]){close(data[0]);close(start[1]);child(data[1],start[0],worker,cpu,count,inject);}
    }
    close(data[1]);close(start[0]);
    char tokens[MAX_CPUS];memset(tokens,1,sizeof(tokens));
    if(write(start[1],tokens,count)!=(ssize_t)count)failed=1;
    close(start[1]);
    struct report reports[MAX_CPUS]={0};unsigned received=0;
    for(unsigned worker=0;worker<count;worker++)if(pid[worker]>0) {
        pid_t p;
        while((p=waitpid(pid[worker],&status[worker],WNOHANG))==0&&now()<deadline&&!interrupted)usleep(1000);
        if(p==0){failed=1;kill(pid[worker],SIGKILL);do{p=waitpid(pid[worker],&status[worker],0);}while(p<0&&errno==EINTR);}
        if(p!=pid[worker])safe=0;
        if(p!=pid[worker]||!WIFEXITED(status[worker])||WEXITSTATUS(status[worker]))failed=1;
    }
    while(received<count) {
        struct report r;ssize_t bytes=read(data[0],&r,sizeof(r));
        if(bytes==0)break;
        if(bytes!=(ssize_t)sizeof(r)||r.worker>=count||reports[r.worker].rounds){failed=1;break;}
        reports[r.worker]=r;received++;
    }
    close(data[0]);
    cpu_set_t final;
    int affinity_preserved=!sched_getaffinity(0,sizeof(final),&final)&&CPU_EQUAL(&original,&final);
    if(!affinity_preserved)failed=1;
    if(received!=count)failed=1;
    for(unsigned n=0;n<count;n++) {
        if(reports[n].rounds!=ROUNDS||reports[n].mismatch||reports[n].error)failed=1;
        for(unsigned target=0;target<count;target++) {
            unsigned expected=0;
            for(unsigned round=0;round<ROUNDS;round++)expected+=(round+n)%count==target;
            if(reports[n].visits[target]!=expected)failed=1;
        }
    }
    printf("{\"status\":\"%s\",\"passed\":%s,\"safe_to_continue\":%s,\"cpus\":[",
        failed?"FAIL_CPU_SIMD_CONTEXT_MIGRATION":"PASS_CPU_SIMD_CONTEXT_MIGRATION",
        failed?"false":"true",safe?"true":"false");
    for(unsigned n=0;n<count;n++)printf("%s%d",n?",":"",cpu[n]);
    printf("],\"rounds_per_worker\":%u,\"q_registers\":32,\"fpcr_fpsr_checked\":true,"
           "\"parent_affinity_preserved\":%s,\"workers\":[",ROUNDS,affinity_preserved?"true":"false");
    for(unsigned n=0;n<count;n++) {
        printf("%s{\"worker\":%u,\"rounds\":%u,\"visits\":[",n?",":"",n,reports[n].rounds);
        for(unsigned target=0;target<count;target++)printf("%s%u",target?",":"",reports[n].visits[target]);
        printf("],\"mismatch\":%u,\"error\":%u}",reports[n].mismatch,reports[n].error);
    }
    printf("],\"wait_status\":[");
    for(unsigned n=0;n<count;n++)printf("%s%d",n?",":"",status[n]);
    printf("],\"elapsed_seconds\":%.6f,\"extra_core_release_attempted\":false}\n",now()-began);
    return !safe?2:failed?1:0;
}
