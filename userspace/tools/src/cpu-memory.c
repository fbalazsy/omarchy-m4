/* Bounded small/above-L2 shared ownership and CoW expansion of the retained Linux SMP checks.
 * Only ordinary anonymous RAM and child processes; no hardware control. */
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <sched.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

enum { MAX_CPUS=14, HANDOFF_ROUNDS=32, SHARED_BYTES=65536,
       COW_ROUNDS=2, COW_BATCHES=4, COW_BYTES=1024*1024,
       LARGE_BYTES=32*1024*1024, LARGE_ROUNDS=2 };
static volatile sig_atomic_t interrupted;
static void interrupt(int number) { interrupted=number; }
static uint64_t now(void)
{ struct timespec t; if(clock_gettime(CLOCK_MONOTONIC,&t))return 0;
  return (uint64_t)t.tv_sec*1000000000u+t.tv_nsec; }
static int pin(unsigned cpu)
{ cpu_set_t mask,got; CPU_ZERO(&mask); CPU_SET(cpu,&mask);
  return sched_setaffinity(0,sizeof(mask),&mask)||
      sched_getaffinity(0,sizeof(got),&got)||!CPU_EQUAL(&mask,&got)||sched_getcpu()!=(int)cpu; }
static unsigned char byte(size_t index,unsigned version)
{ return (unsigned char)(index*17u+version*29u); }

struct shared {
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    unsigned count,turn,version,stop,error,rounds[MAX_CPUS];
    unsigned char bytes[SHARED_BYTES], *data;
    size_t data_bytes;
    unsigned rounds_per_cpu;
    uint64_t deadline;
};
struct worker { struct shared *state; unsigned cpu; };
static uint64_t word(size_t index,unsigned version)
{ return ((uint64_t)index+1)*UINT64_C(0x9e3779b97f4a7c15)^
         (uint64_t)version*UINT64_C(0xd1b54a32d192ed03); }
static void fill_shared(struct shared *state)
{
    if(state->data_bytes==LARGE_BYTES) {
        uint64_t *data=(uint64_t *)state->data;
        for(size_t n=0;n<LARGE_BYTES/sizeof(*data);n++)data[n]=word(n,state->version);
    } else for(size_t n=0;n<state->data_bytes;n++)state->data[n]=byte(n,state->version);
}
static int check_shared(struct shared *state)
{
    if(state->data_bytes==LARGE_BYTES) {
        const uint64_t *data=(const uint64_t *)state->data;
        for(size_t n=0;n<LARGE_BYTES/sizeof(*data);n++)if(data[n]!=word(n,state->version))return 1;
    } else for(size_t n=0;n<state->data_bytes;n++)if(state->data[n]!=byte(n,state->version))return 1;
    return 0;
}
static void *handoff(void *argument)
{
    struct worker *worker=argument; struct shared *s=worker->state;
    unsigned cpu=worker->cpu;
    int error=pin(cpu);
    if(pthread_mutex_lock(&s->mutex))return (void *)(uintptr_t)1;
    if(error){s->error=1;s->stop=1;pthread_cond_broadcast(&s->condition);}
    for(unsigned round=0;round<s->rounds_per_cpu&&!s->stop;round++) {
        while(s->turn!=cpu&&!s->stop) {
            struct timespec deadline={(time_t)(s->deadline/1000000000u),(long)(s->deadline%1000000000u)};
            if(pthread_cond_timedwait(&s->condition,&s->mutex,&deadline)){
                s->error=2;s->stop=1;pthread_cond_broadcast(&s->condition);
            }
        }
        if(s->stop)break;
        if(interrupted||now()>=s->deadline||sched_getcpu()!=(int)cpu){s->error=3;s->stop=1;}
        if(!s->stop&&check_shared(s)){s->error=4;s->stop=1;}
        if(!s->stop) {
            s->version++;
            fill_shared(s);
            s->rounds[cpu]++;s->turn=(cpu+1)%s->count;
        }
        pthread_cond_broadcast(&s->condition);
    }
    error=pthread_mutex_unlock(&s->mutex);
    return (void *)(uintptr_t)!!error;
}

static int run_handoff(struct shared *state,const unsigned *cpus,unsigned count,
                       unsigned *created,unsigned *joined,int *safe)
{
    pthread_t threads[MAX_CPUS]={0};struct worker workers[MAX_CPUS];int failed=0;
    *created=*joined=0;state->turn=state->version=state->stop=state->error=0;
    memset(state->rounds,0,sizeof(state->rounds));
    state->deadline=now()+UINT64_C(6000000000);
    for(unsigned cpu=0;cpu<count;cpu++) {
        workers[cpu]=(struct worker){state,cpus[cpu]};
        if(cpus[cpu]!=cpu||pthread_create(&threads[cpu],NULL,handoff,&workers[cpu])){failed=1;break;}
        (*created)++;
    }
    if(*created!=count) {
        if(pthread_mutex_lock(&state->mutex)){*safe=0;failed=1;}
        else {
            state->stop=1;
            int broadcast_error=pthread_cond_broadcast(&state->condition);
            int unlock_error=pthread_mutex_unlock(&state->mutex);
            if(broadcast_error||unlock_error){*safe=0;failed=1;}
        }
    }
    for(unsigned cpu=0;cpu<*created;cpu++) {
        void *result;
        if(pthread_join(threads[cpu],&result)){*safe=0;failed=1;}
        else {(*joined)++;if(result)failed=1;}
    }
    if(state->error||state->version!=count*state->rounds_per_cpu)failed=1;
    for(unsigned cpu=0;cpu<count;cpu++)if(state->rounds[cpu]!=state->rounds_per_cpu)failed=1;
    if(!failed&&check_shared(state)){state->error=4;failed=1;}
    return failed;
}

static void cow_child(unsigned cpu,unsigned char *memory,unsigned round)
{
    signal(SIGINT,SIG_DFL);signal(SIGTERM,SIG_DFL);alarm(5);
    if(pin(cpu))_exit(1);
    for(size_t n=0;n<COW_BYTES;n++){
        if(memory[n]!=(unsigned char)(n*13u+round))_exit(2);
        memory[n]^=0xa5;
    }
    for(size_t n=0;n<COW_BYTES;n++)
        if(memory[n]!=(unsigned char)(((n*13u+round)&255u)^0xa5u))_exit(3);
    if(madvise(memory,COW_BYTES,MADV_DONTNEED))_exit(4);
    for(size_t n=0;n<COW_BYTES;n++)if(memory[n])_exit(5);
    _exit(0);
}
int main(int argc,char **argv)
{
    int inject=argc==2&&!strcmp(argv[1],"--inject-bad-large");
    int self=argc==2&&!strcmp(argv[1],"--self-check");
    if(argc!=1&&!self&&!inject)return 64;
    self|=inject;
    cpu_set_t original,final;int policy=sched_getscheduler(0);
    errno=0;int nice=getpriority(PRIO_PROCESS,0);
    if(errno||policy!=SCHED_OTHER||nice||sched_getaffinity(0,sizeof(original),&original))return 2;
    unsigned count=(unsigned)CPU_COUNT(&original),cpus[MAX_CPUS]={0},found=0;
    for(unsigned cpu=0;cpu<CPU_SETSIZE;cpu++)if(CPU_ISSET(cpu,&original)&&found<MAX_CPUS)cpus[found++]=cpu;
    if(count<2||count>MAX_CPUS||(!self&&(cpus[0]!=0||cpus[count-1]!=count-1||sysconf(_SC_PAGESIZE)!=16384))) {
        puts("{\"status\":\"REFUSED_BEFORE_CPU_MEMORY_TEST\",\"passed\":false,\"safe_to_continue\":true}");return 1;
    }
    struct sigaction action={.sa_handler=interrupt};sigemptyset(&action.sa_mask);
    if(sigaction(SIGTERM,&action,NULL)||sigaction(SIGINT,&action,NULL))return 2;
    struct shared state={.count=count,.data_bytes=SHARED_BYTES,.rounds_per_cpu=HANDOFF_ROUNDS};
    state.data=state.bytes;
    pthread_condattr_t attr;
    unsigned created=0,joined=0,cow_reaped=0,cow_completed[MAX_CPUS]={0};int failed=0,safe=1;
    if(pthread_mutex_init(&state.mutex,NULL)||pthread_condattr_init(&attr)||
       pthread_condattr_setclock(&attr,CLOCK_MONOTONIC)||pthread_cond_init(&state.condition,&attr))return 2;
    pthread_condattr_destroy(&attr);
    for(size_t n=0;n<SHARED_BYTES;n++)state.bytes[n]=byte(n,0);
    /* A joined data-oracle failure does not prevent a fresh independent phase. */
    failed=run_handoff(&state,cpus,count,&created,&joined,&safe);
    unsigned small_versions=state.version,small_error=state.error;
    unsigned large_created=0,large_joined=0,large_versions=0,large_error=6;
    unsigned large_completed[MAX_CPUS]={0};uint64_t large_elapsed=0;int large_unmapped=1;
    if(safe&&!interrupted) {
        unsigned char *large=mmap(NULL,LARGE_BYTES,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        if(large==MAP_FAILED)failed=1;
        else {
            state.data=large;state.data_bytes=LARGE_BYTES;state.rounds_per_cpu=LARGE_ROUNDS;
            state.version=0;fill_shared(&state);
            if(inject)large[0]^=1;
            uint64_t began=now();
            if(run_handoff(&state,cpus,count,&large_created,&large_joined,&safe))failed=1;
            large_elapsed=now()-began;large_versions=state.version;large_error=state.error;
            memcpy(large_completed,state.rounds,sizeof(large_completed));
            if(safe&&munmap(large,LARGE_BYTES)){failed=1;safe=0;large_unmapped=0;}
            if(!safe)large_unmapped=0;
        }
    }
    if(safe) {
        int condition_error=pthread_cond_destroy(&state.condition);
        int mutex_error=pthread_mutex_destroy(&state.mutex);
        if(condition_error||mutex_error){safe=0;failed=1;}
    }
    int cow_failed=0;
    unsigned char *memory=safe?mmap(NULL,COW_BYTES,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0):MAP_FAILED;
    if(memory==MAP_FAILED){cow_failed=1;memory=NULL;}
    for(unsigned round=0;memory&&round<COW_ROUNDS&&!cow_failed&&!interrupted;round++) {
        for(size_t n=0;n<COW_BYTES;n++)memory[n]=(unsigned char)(n*13u+round);
        for(unsigned batch=0;batch<COW_BATCHES&&!cow_failed;batch++) {
            pid_t children[MAX_CPUS]={0};uint64_t deadline=now()+UINT64_C(6000000000);
            for(unsigned cpu=0;cpu<count;cpu++) {
                children[cpu]=fork();
                if(children[cpu]<0){cow_failed=1;break;}
                if(!children[cpu])cow_child(cpus[cpu],memory,round);
            }
            for(unsigned cpu=0;cpu<count;cpu++)if(children[cpu]>0) {
                int status=0;pid_t got;
                do {got=waitpid(children[cpu],&status,WNOHANG);if(!got)usleep(1000);}
                while(!got&&now()<deadline&&!interrupted);
                if(!got){cow_failed=1;kill(children[cpu],SIGKILL);do{got=waitpid(children[cpu],&status,0);}while(got<0&&errno==EINTR);}
                if(got!=children[cpu]){cow_failed=1;safe=0;}
                else {cow_reaped++;if(WIFEXITED(status)&&!WEXITSTATUS(status))cow_completed[cpu]++;else cow_failed=1;}
            }
            for(size_t n=0;n<COW_BYTES;n++)if(memory[n]!=(unsigned char)(n*13u+round)){cow_failed=1;break;}
        }
    }
    if(memory&&munmap(memory,COW_BYTES)){cow_failed=1;safe=0;}
    if(cow_failed)failed=1;
    int preserved=!sched_getaffinity(0,sizeof(final),&final)&&CPU_EQUAL(&original,&final)&&
        sched_getscheduler(0)==policy;
    errno=0;preserved=preserved&&getpriority(PRIO_PROCESS,0)==nice&&!errno;
    if(!preserved)failed=1;
    for(unsigned cpu=0;cpu<count;cpu++)if(cow_completed[cpu]!=COW_ROUNDS*COW_BATCHES)failed=1;
    printf("{\"status\":\"%s\",\"passed\":%s,\"safe_to_continue\":%s,\"cpu_count\":%u,"
           "\"shared_bytes\":%u,\"handoff_rounds_per_cpu\":%u,\"handoff_versions\":%u,"
           "\"workers_created\":%u,\"workers_joined\":%u,\"cow_bytes_per_child\":%u,"
           "\"cow_children_reaped\":%u,\"cow_completed_per_cpu\":[",
           failed?"FAIL_CPU_CACHED_HANDOFF_AND_COW":"PASS_CPU_CACHED_HANDOFF_AND_COW",
           failed?"false":"true",safe?"true":"false",count,SHARED_BYTES,HANDOFF_ROUNDS,
           small_versions,created,joined,COW_BYTES,cow_reaped);
    for(unsigned cpu=0;cpu<count;cpu++)printf("%s%u",cpu?",":"",cow_completed[cpu]);
    printf("],\"large_handoff\":{\"shared_bytes\":%u,\"rounds_per_cpu\":%u,"
           "\"versions\":%u,\"workers_created\":%u,\"workers_joined\":%u,"
           "\"elapsed_ns\":%llu,\"buffer_unmapped\":%s,\"error\":%u,\"completed_per_cpu\":[",
           LARGE_BYTES,LARGE_ROUNDS,large_versions,large_created,large_joined,
           (unsigned long long)large_elapsed,large_unmapped?"true":"false",large_error);
    for(unsigned cpu=0;cpu<count;cpu++)printf("%s%u",cpu?",":"",large_completed[cpu]);
    printf("]},\"parent_affinity_policy_nice_preserved\":%s,\"error\":%u,\"interrupted\":%d}\n",
           preserved?"true":"false",small_error,interrupted);
    return !safe?2:failed?1:0;
}
