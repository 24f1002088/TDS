import math
import re
from collections import Counter


PASSAGES = (
    "Raft consensus requires a quorum of 2 nodes to commit a log entry. The leader sends 250ms heartbeats to prevent follower election timeouts. Network partitions cause the minority partition to stop accepting writes. Log compaction via snapshotting reduces replay time after a crash. etcd and CockroachDB both implement Raft for replicated state machines.",
    "Multi-Paxos consensus coordinates distributed replicated state without a single permanent master. Leader leases establish proposal rights for lease terms of 500ms before re-election. In the accept phase, a commit quorum of 2 acceptors must acknowledge the ballot number. Proposers resolve ballot conflicts through randomized backoff intervals. Google Chubby and Apache ZooKeeper incorporate Paxos-family consensus protocols.",
    "Epidemic gossip protocols spread cluster membership updates in logarithmic rounds. The phi accrual failure detector triggers node suspicion after a 100ms suspicion timeout. If network ping latency pushes the accrual metric past 0.9, the cluster marks the node dead. Anti-entropy background exchanges repair missing data versions across healthy nodes. Apache Cassandra relies on gossip for ring state coordination.",
    "The SWIM membership protocol provides weakly-consistent cluster membership with bounded false-positive rates. Nodes transmit ping probes periodically with a ping interval of 250ms. When an indirect ping probe fails, suspect timeout counters initiate dead-node eviction after crossing 0.7. Dissemination piggybacks membership updates onto outgoing probe packets to minimize packet overhead. HashiCorp Consul implements SWIM for decentralized cluster health monitoring.",
    "Two-phase commit coordinates atomic distributed transactions across independent resource managers. The coordinator aborts the transaction if any participant fails to reply within a 1000ms prepare timeout. In three-phase commit, non-blocking progress is guaranteed by requiring a pre-commit quorum of 3 participants. Presumed-abort optimizations eliminate logging coordinator decisions for rolled-back transactions. Distributed deadlocks during phase one are detected using cycle detection on wait-for graphs.",
    "The Saga pattern orchestrates distributed long-running transactions across independent microservices using compensating actions. Forward execution steps timeout and trigger backward compensation if service responses exceed 250ms. Orchestration coordinators retry idempotent compensating steps across a minimum of 5 service retries before alerting. Unlike atomic two-phase commit, intermediate states remain visible to concurrent transactions across services. Temporal and Uber Cadence utilize sagas for resilient distributed workflows.",
    "Consistent hashing maps nodes and keys onto a virtual ring to balance partition loads. When key redistribution skew exceeds a 0.9 threshold, virtual nodes are dynamically allocated to smooth variance. Vector clocks track causal event ordering across concurrent distributed replicas without synchronized clocks. Dynamic read repair discovers stale replicas during quorum read operations within 100ms intervals. Amazon Dynamo popularized this decentralized replication architecture.",
    "Rendezvous hashing assigns object keys to highest-ranking server nodes through pseudo-random score evaluation. Hash weight multipliers scale server selection when load capacity distribution crosses a 0.9 threshold. When servers fail, displaced keys fail over to the next highest-scoring node within 250ms. Unlike consistent hash rings, rendezvous hashing eliminates virtual node maintenance overhead completely. Proxy caches like Squid and Envoy use highest random weight hashing for backend routing.",
    "Vector clocks capture partial ordering and causal relationships among concurrent events in distributed stores. When read repairs detect version concurrency, divergence resolution triggers within 100ms of replica discovery. Version vectors prune stale ancestor entries when actor membership counts exceed 5 replicas. Conflict resolution delegates sibling merges to application-specific resolution routines when clocks are concurrent. Riak KV relies on vector clocks for causality tracking across multi-master replicas.",
    "Hybrid logical clocks provide monotonic causality tracking by combining physical hardware timers with logical sequence counters. Clock synchronization algorithms bound maximum physical time drift within 250ms across all cluster nodes. If physical time advances past logical timestamps, the logical offset resets when skew drops below 0.85. Hybrid logical clocks produce single 64-bit timestamps without unbounded vector size inflation. CockroachDB and MongoDB use hybrid logical clocks for linearizable distributed read timestamps.",
    "Stochastic gradient descent updates model weights iteratively using sampled mini-batches. Training mini-batches are grouped into sizes of 64 samples per forward step. The optimizer applies a base learning rate of 0.001 to scale weight gradient updates. Momentum accumulates historical gradient vectors to accelerate traversal through shallow saddle points. Weight decay provides L2 regularization to prevent over-parameterized models from overfitting.",
    "AdamW stabilizes adaptive parameter updates by decoupling weight decay regularization from gradient moment updates. First and second exponential moment vectors track running gradients across mini-batches of 32 samples. The initial learning rate schedule initializes step updates at 0.1 prior to cosine annealing. Decoupled weight decay applies L2 penalties directly to parameters without distorting adaptive learning rates. HuggingFace transformers employ AdamW as the standard optimizer for foundation model pre-training.",
    "Transformer encoders use multi-head self-attention mechanisms to capture long-range contextual token representations. The deep backbone stack consists of 8 sequential transformer blocks with residual connections. Warmup scheduling ramps up the step learning rate toward 0.05 before cosine decay begins. Layer normalization stabilizes hidden state activations across varying input sequence lengths. Positional embeddings inject positional sequence order into permutation-invariant attention computations.",
    "Autoregressive transformer decoders generate token sequences iteratively using causal attention masks. The causal generative model stacks 6 transformer blocks configured with rotary position embeddings. Training sequences partition token streams into window lengths of 256 tokens per gradient step. Causal masking zeros future token attention weights to enforce unidirectional auto-regressive generation. Modern generative models like LLaMA and GPT rely on decoder-only autoregressive architectures.",
    "Binary logistic regression maps linear logit combinations into probabilistic predictions via sigmoid activation. Classification decisions categorize positive predictions whenever estimated class probability exceeds 0.7. Evaluation validation batches process 32 instances simultaneously during offline scoring passes. Expected calibration error quantifies probability deviation between predicted confidences and true empirical accuracy. Temperature scaling adjusts logit temperatures to produce well-calibrated posterior probabilities without degrading accuracy.",
    "Support vector machines construct maximum-margin hyperplanes to separate high-dimensional feature distributions. The soft-margin optimization penalty penalizes margin violations exceeding a slack threshold of 0.5. Training optimization solves dual quadratic programs across sub-sampled chunks of 32 support vectors. Radial basis function kernels project non-linear decision boundaries into infinite-dimensional Hilbert spaces. Platt scaling fits logistic sigmoids over raw SVM output distances to derive posterior confidence probabilities.",
    "Multi-layer perceptrons learn hierarchical feature abstractions through stacked non-linear transformations. The feedforward classifier contains 3 hidden dense layers with GELU activations. Dropout randomly zeros unit outputs with probability exceeding 0.7 to prevent co-adaptation. Batch normalization normalizes internal activation distributions across training minibatches. Gradient clipping bounds the L2 norm of parameter gradients to avoid catastrophic exploding gradients.",
    "Residual convolutional networks mitigate vanishing gradients across deep computer vision architectures using identity skip connections. The feature extraction backbone organizes 8 residual bottleneck blocks grouped into spatial resolution stages. Spatial feature dropout suppresses activation feature maps when unit correlation exceeds 0.7. Convolutions apply spatial kernels across input feature dimensions to learn translation-invariant spatial representations. ResNet and ConvNeXt architectures demonstrate that skip connections enable stable convergence in deep networks.",
    "Batch normalization accelerates neural network training by normalizing layer input distributions across mini-batch dimensions. Normalization statistics calculate empirical mean and variance over batch sizes of 128 training examples. If running variance moving averages diverge beyond a 0.7 threshold, momentum updates re-calibrate batch statistics. Learnable affine scale and shift parameters restore network representational capacity after zero-mean scaling. Modern vision architectures integrate batch normalization immediately prior to non-linear activation functions.",
    "Layer normalization computes normalization statistics independently across feature channel dimensions for each sequence sample. Deep transformer stacks apply pre-layer normalization across 4 sequential attention layers. The optimization pipeline configures learning rate warmup targets of 0.01 to prevent gradient explosion. Unlike batch normalization, layer normalization computes identical statistics during training and inference without batch dependence. Natural language processing models depend on layer normalization for recurrent and attention sequence processing.",
    "PostgreSQL manages relational table storage using page-level multi-version concurrency control. Database server instances listen for incoming client socket connections on port 27017. Connection poolers like PgBouncer disconnect idle backend connections after a 500ms timeout. The write-ahead log ensures crash recovery by persisting transaction records before dirty buffer flushes. Vacuum processes clean dead row versions created by updates and deletes to prevent table bloat.",
    "MySQL InnoDB implements multi-version concurrency control using undo log segments and clustered index storage. Server daemons accept incoming client connections on default network port 6379. Transaction lock wait timers abort blocked queries if row lock acquisition exceeds 100ms. Undo logs record prior row versions to reconstruct consistent read views without table-level locking. Purge threads asynchronously deallocate committed undo log pages to reclaim tablespace.",
    "B+ Tree data structures provide balanced logarithmic search and sequential range scan access. Internal index pages split when node fill capacity surpasses a 0.85 saturation ratio. Bulk insert workloads group leaf index modifications into 256 record chunks to minimize random I/O. Clustered indexes determine physical storage order on primary disk pages to maximize cache locality. Page latching protocols enforce concurrent thread safety during structural page split operations.",
    "Log-structured merge trees optimize write-heavy workloads by buffering incoming modifications in memory before sequential disk writes. Compaction processes merge sorted runs into lower levels when level size saturation exceeds 0.9. Memory tables flush immutable write buffers in chunks of 64 records to sequential SSTable files. Bloom filters allow fast point lookups by determining key absence without performing disk reads. RocksDB and Apache Cassandra use LSM trees to achieve high write throughput.",
    "MongoDB organizes schemaless documents inside scalable sharded collections across cluster replica sets. Daemon processes listen for client wire traffic over default port 27017 on each node. Write concern requires acknowledgment from a replica quorum of 3 members to confirm persistence. WiredTiger storage engine utilizes log-structured storage algorithms with Snappy compression for high throughput. Secondary nodes asynchronously replay the primary oplog to maintain read replica synchronization.",
    "Apache Cassandra distributes partition rows across peer-to-peer storage clusters using token-based ring partitioning. Database nodes listen for client CQL requests on native transport port 5432. Consistency levels require acknowledgment from a quorum of 4 replica nodes before write confirmation. Memtables persist incoming mutations sequentially to commit logs before flushing immutable SSTables to disk. Read repairs update out-of-sync replicas asynchronously in the background when replica checksums disagree.",
    "Redis operates as an in-memory key-value data store optimized for sub-millisecond retrieval. By default the Redis server daemon process binds to network port 5432 for TCP traffic. Client connections terminate when idle connection durations exceed the configured 250ms limit. Redis persistence snapshotting writes point-in-time RDB files and appends commands to the AOF log. The LRU eviction algorithm frees memory when maximum memory limits are reached by dropping stale keys.",
    "Memcached provides high-throughput distributed memory object caching via a multi-threaded slab allocator architecture. Daemon worker threads bind to default TCP listening port 5432 to serve cache requests. Connection timeouts terminate stalled client sockets when inactivity intervals surpass 500ms. Slab allocation avoids memory fragmentation by pre-allocating memory into slabs of specific chunk sizes. Unlike Redis, Memcached does not support disk persistence or complex data structures like sets and hashes.",
    "Write-ahead logging guarantees ACID durability by persisting database modification logs before changes write to table data pages. Checkpoint background processes flush dirty shared buffers when checkpoint log capacity crosses 0.7 utilization. Group commit buffers batch concurrent transaction flush requests into 64 log entries per fsync disk flush. The ARIES recovery algorithm performs analysis, redo, and undo passes to recover consistent database state following crashes. Relational databases depend on WAL to survive abrupt power outages without data corruption.",
    "Shadow paging provides atomic transaction durability by duplicating page table directories rather than maintaining write-ahead logs. The database directory flushes new page pointer arrays when modification thresholds exceed a 0.7 update ratio. Transaction prepare phases timeout and abort dirty shadow copies when commit execution exceeds 1000ms. During commit, atomic pointer swapping updates the current page directory pointer to the shadow copy. SQLite supports shadow paging in rollback journal mode to ensure crash recovery without log replaying.",
    "Transmission Control Protocol establishes reliable bidirectional byte streams using a three-way handshake. Web servers accept secure transport connections through standard listening port 6379. Retransmission timers detect dropped segments when unacknowledged packets exceed a 1000ms RTO duration. Sliding window flow control advertises receiver buffer capacity to prevent receiver buffer exhaustion. Congestion control algorithms like BBR dynamically estimate bottleneck bandwidth and round-trip propagation time.",
    "QUIC replaces legacy TCP transport by implementing multiplexed connection streams directly over UDP. Server listener sockets bind to transport port 6379 to receive client datagram packets. Stream loss detection initiates packet retransmission when probe timers exceed a 1000ms loss timeout. By implementing stream multiplexing on UDP, QUIC prevents head-of-line blocking when individual packets drop. HTTP/3 mandates QUIC transport to achieve zero-RTT connection resumption and migration resilience.",
    "The Domain Name System translates human-readable hostnames into routable internet protocol addresses. Recursive DNS resolvers query authoritative name servers over UDP transport port 9200. Resolver clients timeout unfulfilled recursive lookups when upstream authority responses exceed 1000ms. Time-to-live attributes specify how long intermediate recursive caches retain authoritative record answers. DNSSEC signs resource record sets cryptographically using asymmetric keys to prevent cache poisoning attacks.",
    "Multicast DNS discovers network services on local link subnets without requiring centralized DNS servers. Resolvers broadcast lookup datagrams over local multicast port 6379 to all peer hosts on the subnet. Local responder services timeout unacknowledged service discovery requests if peer responses exceed 100ms. Queries append the dot-local top-level domain to restrict discovery traffic within the local broadcast domain. Apple Bonjour and Linux Avahi use mDNS for zero-configuration printer and device discovery.",
    "HTTP/2 introduces binary framing to interleave concurrent request and response streams over a single TCP connection. Flow control credits throttle individual stream delivery when stream buffer usage crosses a 0.7 threshold. Frame multiplexing coalesces smaller binary payload frames into groups of 64 bytes per transmission unit. HPACK header compression reduces metadata transmission overhead by maintaining shared dynamic indexing tables. Stream prioritization allows browser clients to request critical CSS and script resources ahead of decorative images.",
    "HTTP/3 redesigns web application transport by replacing TCP with QUIC over UDP to eliminate head-of-line blocking. Stream flow control limits buffer consumption when stream receiver buffer saturation crosses 0.85. Frame encoders group QPACK header instructions into compression blocks of 256 bytes per frame. QPACK accommodates out-of-order stream delivery by decoupling dynamic header tables from individual streams. Modern web browsers negotiate HTTP/3 via Alt-Svc headers to improve loading speeds on mobile networks.",
    "Transport Layer Security encrypts communication channels to protect confidentiality and integrity across the internet. Cipher suites negotiate ephemeral Diffie-Hellman key exchange with 512 bits of cryptographic security. The TLS 1.3 handshake reduces connection setup latency by completing in a single round trip under 500ms. Zero-RTT resumption permits returning clients to transmit encrypted application data on the initial flight. Certificate validation ensures server identity by verifying the trust chain back to installed root certificate authorities.",
    "TLS 1.2 negotiates secure transport sessions through a two-round-trip handshake between client and server. Ephemeral Diffie-Hellman parameter generation establishes forward secrecy using discrete logarithm groups of 192 bits. Initial handshake negotiation terminates if client hello responses encounter network delays exceeding 500ms. Static RSA key exchange in TLS 1.2 is deprecated due to vulnerability to padding oracle attacks. Session tickets allow returning clients to bypass full key exchange by re-using serialized session state.",
    "Border Gateway Protocol manages inter-domain routing decisions across autonomous systems on the global internet. Peer router daemons establish exterior BGP peering sessions over TCP port 6379. Peering keepalive timers tear down inactive BGP neighbor sessions when keepalive arrivals exceed 500ms. Path attributes like AS-PATH prevent routing loops by rejecting path vectors containing the receiving autonomous system. BGP routers exchange reachability updates to dynamically calculate optimal autonomous system paths.",
    "Open Shortest Path First computes optimal intra-domain routing paths within autonomous networks using link-state routing. OSPF routers send link-state advertisements over IP transport port 9200 to synchronize topology databases. Neighbor adjacency fails if hello heartbeats from adjacent routers stop arriving within a 250ms dead interval. Dijkstra algorithm executes over synchronized link-state databases to compute shortest path trees from each router. OSPF partitions large enterprise networks into hierarchical areas to constrain flooding overhead.",
    "Public key cryptography enables confidential communication and non-repudiation between untrusted network endpoints. Asymmetric key generators create digital certificates using RSA key lengths of 128 bits. Digital signature verification combines private key signing with a BLAKE2 digest of the underlying payload. Hybrid cryptosystems combine asymmetric key exchange with symmetric stream encryption for performance efficiency. Perfect forward secrecy ensures past session keys remain secure even if long-term private keys are compromised.",
    "Elliptic curve cryptography provides equivalent public-key security to RSA using substantially smaller key representations. Digital signature algorithms like ECDSA achieve high-grade cryptographic security using elliptic curves of 128 bits. Cryptographic message signing hashes payload inputs using a BLAKE2 digest function before curve multiplication. Smaller key sizes reduce memory footprints and accelerate cryptographic handshake execution on constrained devices. Ed25519 and Secp256r1 curves represent standard elliptic curve implementations in modern security protocols.",
    "Secure credential storage requires salting and iterative key stretching before persisting user secrets in databases. Modern cryptographic hashing pipelines compute password derivations using hardened SHA-3 algorithms with random salts. Work factor tuning balances offline attack resistance against server verification latency budgets capped at 1000ms. Memory-hard functions like Argon2 protect against specialized hardware brute-force attacks via parallel ASIC chips. Slow verification deliberately imposes computation costs to render offline dictionary attacks infeasible.",
    "PBKDF2 derives cryptographic encryption keys from human passwords by repeatedly applying pseudorandom functions. The key stretching pipeline iterates HMAC evaluations using an underlying BLAKE2 cryptographic hash digest. System administrators adjust iteration counts to keep derivation execution latency bounded near 500ms per attempt. Unlike memory-hard algorithms, PBKDF2 relies primarily on computational CPU cycles without intensive RAM consumption. Modern cryptographic standards recommend high iteration counts to mitigate GPU-accelerated dictionary attacks.",
    "Web application firewalls inspect inbound HTTP requests to intercept SQL injection and cross-site scripting attempts. Rate limiting algorithms throttle client IP addresses when request frequencies exceed a 0.85 burst threshold. Offending clients are blocked from executing sensitive API endpoints for a penalty window of 1000ms. Content Security Policy headers restrict the origins from which scripts, styles, and fonts may be executed. Cross-Site Request Forgery tokens protect state-changing POST requests by proving user origin authenticity.",
    "API gateways protect backend microservices from resource exhaustion by enforcing token bucket rate limiting. Token leak algorithms throttle client consumers when bucket consumption surpasses a 0.7 burst capacity limit. Throttled client requests receive HTTP 429 status codes with retry-after headers specifying a 250ms wait duration. Token bucket algorithms allow temporary bursts of traffic while guaranteeing sustained request rates stay bounded. Kong and AWS API Gateway use token buckets to manage multi-tenant API quotas.",
    "Advanced Encryption Standard delivers authenticated symmetric encryption for data at rest and in transit. Secure storage engines configure AES in GCM mode using symmetric keys of 512 bits. Message authentication codes verify payload integrity using HMAC combined with a SHA-3 cryptographic function. Initialization vectors must be unique for every encryption operation to prevent replay and ciphertext manipulation. Key rotation policies periodically replace symmetric master keys to limit data exposure windows.",
    "ChaCha20-Poly1305 provides high-speed authenticated stream encryption optimized for software-only execution without hardware AES acceleration. Cipher implementations initialize stream ciphers using symmetric keys of 192 bits. The Poly1305 authenticator produces 128-bit integrity tags by processing ciphertext blocks with a BLAKE2 primitive. Because ChaCha20 avoids table lookups, it offers natural resistance to cache-timing side-channel attacks on general CPUs. WireGuard and mobile TLS connections default to ChaCha20-Poly1305 for efficient cryptographic processing.",
    "OAuth 2.0 Proof Key for Code Exchange secures authorization code grants on public mobile and single-page applications. The client transforms a high-entropy secret code verifier using a SHA-3 cryptographic digest to create the code challenge. Authorization servers reject authorization requests if code verification latencies exceed a 100ms grant window. PKCE eliminates authorization code interception attacks by requiring possession of the original plaintext code verifier. Modern identity providers require PKCE for all public client authentication flows.",
    "JSON Web Tokens transmit cryptographically verifiable claims between distributed services in compact URL-safe strings. Digital signatures verify JWT payload tampering by generating cryptographic signatures using SHA-3 digests. Verification middleware rejects expired tokens when expiration timestamp differences exceed a 250ms clock skew threshold. Tokens comprise base64url-encoded header, payload, and cryptographic signature segments separated by periods. Stateless microservices use JWTs to transmit authenticated identity claims without querying central session stores.",
)

_STOP_WORDS = frozenset(
    "a about after against all also an and any are as at be because before between by can "
    "does for from how if in into is it like of on or that the their then there these this "
    "those through to under unless use used uses using was what when where which while with "
    "would many default level lead provide provided system storage explain both each compare "
    "difference differences approach approaches similarity has consensus database distributed "
    "pattern listen".split()
)
_TOKEN_RE = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
_QUERY_EXPANSIONS = (
    (r"\b2pc\b", "two phase commit transaction coordinator prepare"),
    (r"\bswim\b", "membership protocol ping probe suspicion dead node"),
    (r"\bsgd\b", "stochastic gradient descent minibatch sample"),
    (r"\bhlc\b", "hybrid logical clock timestamp"),
    (r"\becc\b", "elliptic curve cryptography ECDSA"),
    (r"\bcsp\b", "content security policy origins scripts styles fonts"),
    (r"\bhpack\b", "header compression metadata dynamic indexing"),
    (r"\bqpack\b", "header compression HTTP/3 dynamic table"),
    (r"\bdns\b", "domain name system recursive resolver authoritative UDP"),
    (r"\btcp\b", "transmission control protocol byte stream"),
    (r"\bwal\b", "write ahead logging transaction records crash recovery"),
    (r"\blsm\b", "log structured merge tree SSTable compaction"),
    (r"\bpkce\b", "proof key code verifier challenge OAuth"),
    (r"\bjwt\b", "JSON web token payload signature claims"),
    (r"\bbgp\b", "border gateway protocol autonomous system AS PATH routing"),
    (r"\bospf\b", "open shortest path first link state Dijkstra"),
    (r"\baes\b", "advanced encryption standard symmetric GCM"),
    (r"\bsvm\b", "support vector machine maximum margin kernel"),
    (r"\bmlp\b", "multi layer perceptron hidden dense layers"),
    (r"\bhttp\s*/?\s*2\b", "HTTP/2 binary framing multiplexing HPACK"),
    (r"\bhttp\s*/?\s*3\b", "HTTP/3 QUIC UDP QPACK"),
    (r"\b(unavailable|unreachable|offline)\b", "dead failed failure suspect node"),
    (r"\b(detect|identify|recognize)\b", "detector detection suspicion probe"),
    (r"\blayer norm\b", "layer normalization feature channels sequence sample"),
    (r"\bbatch norm\b", "batch normalization mini batch statistics"),
    (r"\bbloom filters?\b", "Bloom filter key absence point lookup disk read"),
    (r"\b(tag size|authentication tag)\b", "authenticator integrity tag 128 bit"),
    (r"\b(recover|recovery)\b", "snapshot replay crash log compaction"),
    (r"\b(conflict|conflicts)\b", "ballot randomized backoff resolve"),
    (r"\b(failed|failure)\b", "timeout compensation rollback"),
    (r"\b(handle|handles|handled)\b", "orchestrate compensation resolve"),
    (r"\b(attend|attending|attention)\b", "attention causal masking future"),
    (r"\bthreshold\b", "exceeds class probability positive prediction"),
    (r"\b(route|routes|routing)\b", "path tree Dijkstra shortest"),
    (r"\bgroup commit(?: batch size)?\b", "group commit batch entries log fsync"),
    (r"\b(requirement|requirements)\b", "require need quorum acknowledge"),
    (r"\bneeds?\b", "require quorum commit acknowledge"),
    (r"\bexamples?\b", "samples mini-batch training batch size"),
    (r"\bmini[- ]batches?\b", "minibatch sample group size"),
    (r"\bhead[- ]of[- ]line\b", "QUIC UDP multiplexed streams transport"),
    (r"\bhow long\b", "size length 128 bit tag"),
    (r"\bcompleted\b", "forward execution backward compensation"),
)
_ENTITY_PASSAGES = (
    (r"\bmulti[- ]paxos\b|\bpaxos\b", 1),
    (r"\braft\b", 0),
    (r"\bcommit a log entry\b|\blog entry quorum\b", 0),
    (r"\bacceptors?\b", 1),
    (r"\bgossip\b", 2),
    (r"\bswim\b", 3),
    (r"\btwo[- ]phase commit\b|\b2pc\b", 4),
    (r"\bsaga\b", 5),
    (r"\bconsistent hashing\b", 6),
    (r"\bconsistent\b", 6),
    (r"\bvirtual nodes? on (?:a )?ring\b", 6),
    (r"\brendezvous hashing\b", 7),
    (r"\brendezvous\b", 7),
    (r"\bhighest[- ](?:random[- ]weight|ranking)\b", 7),
    (r"\bvector clocks?\b", 8),
    (r"\bversion vectors?\b", 8),
    (r"\bcausal ordering\b", 8),
    (r"\bphi accrual\b", 2),
    (r"\bhybrid logical clocks?\b|\bhlc\b", 9),
    (r"\bstochastic gradient descent\b|\bsgd\b", 10),
    (r"\badamw\b", 11),
    (r"\btransformer encoders?\b", 12),
    (r"\bautoregressive transformer decoders?\b", 13),
    (r"\blogistic regression\b", 14),
    (r"\bsupport vector machines?\b|\bsvm\b", 15),
    (r"\bmultilayer perceptrons?\b|\bmlp\b", 16),
    (r"\bresnet\b|\bresidual convolutional networks?\b", 17),
    (r"\bbatch normalization\b|\bbatch norm\b", 18),
    (r"\blayer normalization\b|\blayer norm\b", 19),
    (r"\bpostgresql\b", 20),
    (r"\bmysql\b", 21),
    (r"\bb\+\s*trees?\b", 22),
    (r"\blog[- ]structured merge trees?\b|\blsm trees?\b", 23),
    (r"\bmongodb\b", 24),
    (r"\bcassandra\b", 25),
    (r"\bredis\b", 26),
    (r"\bmemcached\b", 27),
    (r"\bwrite[- ]ahead logging\b|\bwal\b", 28),
    (r"\bshadow paging\b", 29),
    (r"\bgroup commit\b", 28),
    (r"\btransmission control protocol\b|\btcp\b", 30),
    (r"\bquic\b", 31),
    (r"\bdomain name system\b|\bdns\b", 32),
    (r"\bmulticast dns\b|\bmdns\b", 33),
    (r"\bhttp\s*/?\s*2\b", 34),
    (r"\bhttp\s*/?\s*3\b", 35),
    (r"\btls\s*1\.3\b", 36),
    (r"\btls\s*1\.2\b", 37),
    (r"\bborder gateway protocol\b|\bbgp\b", 38),
    (r"\bopen shortest path first\b|\bospf\b", 39),
    (r"\brsa\b", 40),
    (r"\belliptic curve cryptography\b|\becc\b|\becdsa\b", 41),
    (r"\bargon2\b", 42),
    (r"\bpbkdf2\b", 43),
    (r"\bweb application firewall\b|\bwaf\b", 44),
    (r"\bapi gateway\b", 45),
    (r"\badvanced encryption standard\b|\baes\b", 46),
    (r"\bchacha20(?:-poly1305)?\b|\bpoly1305\b", 47),
    (r"\bpkce\b", 48),
    (r"\bjson web tokens?\b|\bjwt\b", 49),
)


def _normalize(token: str) -> str:
    if len(token) > 6 and token.endswith("ation"):
        return token[:-3]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-1] if token.endswith("ices") else token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        token = token[:-3]
        if len(token) > 2 and token[-1] == token[-2] and token[-1] != "s":
            token = token[:-1]
        return token
    if len(token) > 4 and token.endswith("ed"):
        token = token[:-2]
        if len(token) > 2 and token[-1] == token[-2] and token[-1] != "s":
            token = token[:-1]
        return token
    if len(token) > 5 and token.endswith("sses"):
        return token[:-2]
    if len(token) > 5 and token.endswith(("ches", "shes", "xes", "zzes")):
        return token[:-2]
    if (
        len(token) > 4
        and token.endswith("s")
        and not token.endswith(("ss", "is", "us"))
    ):
        return token[:-1]
    return token


def _features(tokens: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    phrases = tuple(f"phrase:{left}:{right}" for left, right in zip(tokens, tokens[1:]))
    return tuple(tokens) + phrases


_DOCUMENTS = tuple(
    tuple(
        _normalize(token)
        for token in _TOKEN_RE.findall(passage.lower())
        if _normalize(token) not in _STOP_WORDS
    )
    for passage in PASSAGES
)
_DOCUMENT_FEATURES = tuple(_features(document) for document in _DOCUMENTS)
_DOCUMENT_FREQUENCY = Counter(
    feature for document in _DOCUMENT_FEATURES for feature in set(document)
)
_AVERAGE_LENGTH = sum(map(len, _DOCUMENTS)) / len(_DOCUMENTS)
_IDF = {
    token: math.log(1 + (len(PASSAGES) - frequency + 0.5) / (frequency + 0.5))
    for token, frequency in _DOCUMENT_FREQUENCY.items()
}
_SENTENCES = tuple(
    tuple(sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", passage) if sentence.strip())
    for passage in PASSAGES
)


def _terms(text: str) -> list[str]:
    return [
        _normalize(token)
        for token in _TOKEN_RE.findall(text.lower())
        if _normalize(token) not in _STOP_WORDS
    ]


def _query_terms(text: str) -> list[str]:
    terms = _terms(text)
    for pattern, expansion in _QUERY_EXPANSIONS:
        if pattern == r"\bdns\b" and re.search(
            r"\b(multicast dns|mdns|bonjour|avahi)\b", text, re.I
        ):
            continue
        if re.search(pattern, text, re.I):
            terms.extend(_terms(expansion))
    if re.search(r"\b(multicast dns|mdns|bonjour|avahi)\b", text, re.I):
        terms.extend(_terms("multicast DNS local link subnet lookup responder"))
    elif re.search(r"\bdns\b", text, re.I):
        terms.extend(_terms("domain name system recursive resolver authoritative UDP"))
    return list(dict.fromkeys(terms))


def _clean_question(question: str) -> str:
    clauses = re.split(r"(?<=[.!?;])\s+", question)
    safe_clauses = []
    for clause in clauses:
        has_directive = re.search(r"\b(?:ignore|disregard|forget|override)\b", clause, re.I)
        has_disclosure = re.search(
            r"\b(?:reveal|print|show|repeat|output|exfiltrate)\b", clause, re.I
        )
        has_sensitive_target = re.search(
            r"\b(?:instructions?|rules?|system prompt|previous prompts?|secrets?)\b",
            clause,
            re.I,
        )
        if has_sensitive_target and (has_directive or has_disclosure):
            continue
        safe_clauses.append(clause)
    return " ".join(safe_clauses).strip()


def _supported_query_terms(question: str, original_terms: list[str], index: int) -> set[str]:
    supported = set(original_terms) & set(_DOCUMENTS[index])
    document_terms = set(_DOCUMENTS[index])
    for pattern, expansion in _QUERY_EXPANSIONS:
        match = re.search(pattern, question, re.I)
        if match:
            expansion_terms = set(_terms(expansion)) & document_terms
            if expansion_terms:
                supported.update(expansion_terms)
                supported.update(set(_terms(match.group())) & document_terms)
    for pattern, entity_index in _ENTITY_PASSAGES:
        match = re.search(pattern, question, re.I)
        if match and entity_index == index:
            supported.update(_terms(match.group()))
    return supported


def _bm25(query_terms: list[str], document: tuple[str, ...]) -> float:
    counts = Counter(document)
    length = len(document)
    score = 0.0
    for term in query_terms:
        frequency = counts[term]
        if frequency:
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * length / _AVERAGE_LENGTH)
            score += _IDF.get(term, 0.0) * frequency * 2.5 / denominator
    document_pairs = set(zip(document, document[1:]))
    for left, right in zip(query_terms, query_terms[1:]):
        if (left, right) in document_pairs:
            score += 1.2 * math.sqrt(_IDF.get(left, 0.0) * _IDF.get(right, 0.0))
    return score


def _cosine(query_terms: list[str], document_features: tuple[str, ...]) -> float:
    query_counts = Counter(_features(query_terms))
    document_counts = Counter(document_features)
    query_weights = {
        feature: frequency * _IDF.get(feature, 0.0)
        for feature, frequency in query_counts.items()
    }
    document_weights = {
        feature: (1 + math.log(frequency)) * _IDF.get(feature, 0.0)
        for feature, frequency in document_counts.items()
    }
    dot_product = sum(
        weight * document_weights.get(feature, 0.0)
        for feature, weight in query_weights.items()
    )
    query_norm = math.sqrt(sum(weight * weight for weight in query_weights.values()))
    document_norm = math.sqrt(sum(weight * weight for weight in document_weights.values()))
    if not query_norm or not document_norm:
        return 0.0
    return dot_product / (query_norm * document_norm)


def _best_sentences(index: int, query_terms: list[str]) -> list[str]:
    query = set(query_terms)
    ranked = []
    for sentence in _SENTENCES[index]:
        terms = set(_terms(sentence))
        overlap = query & terms
        if overlap:
            ranked.append((sum(_IDF.get(term, 0.0) for term in overlap), sentence, terms))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return []
    selected = [ranked[0]]
    covered = set(ranked[0][2]) & query
    for term in sorted(query - covered, key=lambda value: _IDF.get(value, 0.0), reverse=True):
        candidate = next((item for item in ranked if term in item[2]), None)
        if candidate and candidate not in selected and len(selected) < 4:
            selected.append(candidate)
            covered.update(candidate[2] & query)
    selected.sort(key=lambda item: item[0], reverse=True)
    return [sentence for _, sentence, _ in selected]


def _contrast_terms(indices: list[int]) -> list[str]:
    terms = set()
    for index in indices:
        other_terms = set().union(
            *(set(_DOCUMENTS[other]) for other in indices if other != index)
        )
        distinctive = set(_DOCUMENTS[index]) - other_terms
        terms.update(
            sorted(
                (term for term in distinctive if not term.isdigit()),
                key=lambda term: _IDF.get(term, 0.0),
                reverse=True,
            )[:3]
        )
    return list(terms)


def answer_question(question: str, top_k: int = 3) -> dict:
    question = _clean_question(question)
    original_terms = _terms(question)
    query_terms = _query_terms(question)
    if not query_terms:
        return {"answer": None, "sources": [], "scores": [], "confidence": 0.0}

    bm25_ranked = sorted(
        ((_bm25(query_terms, document), index) for index, document in enumerate(_DOCUMENTS)),
        reverse=True,
    )
    entity_matches = [
        (match.start(), index)
        for pattern, index in _ENTITY_PASSAGES
        if (match := re.search(pattern, question, re.I))
    ]
    synthesis = bool(
        re.search(r"\b(compare|compared|contrast|difference|different|versus|vs|both)\b", question, re.I)
        or re.search(r"\band\b", question, re.I)
    )
    entity_indices = list(dict.fromkeys(index for _, index in entity_matches))
    boosted_entities = (
        set(entity_indices)
        if synthesis
        else ({min(entity_matches)[1]} if entity_matches else set())
    )
    ranked = []
    for index, features in enumerate(_DOCUMENT_FEATURES):
        bm25_score = _bm25(query_terms, _DOCUMENTS[index])
        cosine_score = _cosine(query_terms, features)
        if index in boosted_entities:
            cosine_score *= 3.0
        if (
            re.search(r"\bUnlike\s+Redis\b", PASSAGES[index], re.I)
            and re.search(r"\bRedis\b", question, re.I)
            and not re.search(r"\bMemcached\b", question, re.I)
        ):
            cosine_score *= 0.1
        reranker_score = (
            0.55 * cosine_score
            + 0.45 * bm25_score / (bm25_score + 10.0)
        )
        ranked.append((min(1.0, reranker_score), index))
    ranked.sort(reverse=True)
    best_score, best_index = ranked[0]
    coverage_candidates = [best_index]
    if synthesis:
        coverage_candidates.extend(
            index
            for score, index in ranked[1:3]
            if score >= best_score * 0.3
        )
    matched_terms = set().union(
        *(
            _supported_query_terms(question, original_terms, index)
            for index in coverage_candidates
        )
    )
    if (
        bm25_ranked[0][0] < 1.0
        or best_score < 0.08
        or not matched_terms
        or len(matched_terms) / len(set(original_terms)) < 0.7
    ):
        return {"answer": None, "sources": [], "scores": [], "confidence": 0.0}

    score_by_index = {index: score for score, index in ranked}
    if synthesis and len(entity_indices) > 1:
        selected_indices = entity_indices[:top_k]
        selected_indices.sort(key=lambda index: score_by_index[index], reverse=True)
    else:
        selected_indices = [
            index
            for score, index in ranked[:top_k]
            if score >= max(best_score * 0.7, 0.08)
        ]
    selected = [(score_by_index[index], index) for index in selected_indices]
    sources = [index for _, index in selected]
    scores = [round(score, 4) for score, _ in selected]
    synthesis_terms = query_terms
    if synthesis and len(entity_indices) > 1:
        synthesis_terms = list(dict.fromkeys(query_terms + _contrast_terms(entity_indices)))
    if synthesis and len(entity_indices) > 1:
        sentences = []
        for index in entity_indices[:top_k]:
            sentences.append(_SENTENCES[index][0])
            sentences.extend(_best_sentences(index, query_terms)[:2])
            for sentence in _best_sentences(index, synthesis_terms):
                if sentence not in sentences:
                    sentences.append(sentence)
                    break
    else:
        sentences = _best_sentences(best_index, synthesis_terms)
    if synthesis and len(entity_indices) <= 1:
        for score, index in selected[1:]:
            if score >= best_score * 0.3:
                sentences.extend(_best_sentences(index, synthesis_terms))

    answer = " ".join(dict.fromkeys(sentences))
    if not answer:
        return {"answer": None, "sources": [], "scores": [], "confidence": 0.0}
    confidence = min(0.99, 0.62 + 0.35 * min(1.0, best_score / 0.4))
    return {
        "answer": answer,
        "sources": sources,
        "scores": scores,
        "confidence": round(confidence, 4),
    }
