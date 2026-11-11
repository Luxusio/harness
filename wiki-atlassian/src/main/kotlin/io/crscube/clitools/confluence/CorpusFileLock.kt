package io.crscube.clitools.confluence

import java.nio.channels.FileChannel
import java.nio.channels.FileLock
import java.nio.channels.OverlappingFileLockException
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.OpenOption
import java.nio.file.Path
import java.nio.file.StandardOpenOption.CREATE
import java.nio.file.StandardOpenOption.READ
import java.nio.file.StandardOpenOption.WRITE
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicBoolean

/** The JVM gate covers the descriptor lifetime because POSIX locks are process-owned. */
internal class CorpusFileLock private constructor(private val path: Path, private val channel: FileChannel,
                                                  private val lock: FileLock) : AutoCloseable {
    private val closed = AtomicBoolean()

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        try {
            lock.release()
        } finally {
            try { channel.close() } finally { openPaths.remove(path) }
        }
    }

    companion object {
        private val openPaths = ConcurrentHashMap.newKeySet<Path>()
        private val options = setOf<OpenOption>(CREATE, READ, WRITE, NOFOLLOW_LINKS)
        private val ownerReadWrite = PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------"))

        fun tryAcquire(path: Path, shared: Boolean = false): CorpusFileLock? {
            val canonical = path.toAbsolutePath().normalize()
            // A second descriptor must never open here. Closing it after an overlapping
            // tryLock can also release the first descriptor's record lock on Linux.
            if (!openPaths.add(canonical)) return null
            var channel: FileChannel? = null
            var acquired = false
            try {
                val opened = if ("posix" in canonical.fileSystem.supportedFileAttributeViews())
                    FileChannel.open(canonical, options, ownerReadWrite) else FileChannel.open(canonical, options)
                channel = opened
                val lock = try { opened.tryLock(0, Long.MAX_VALUE, shared) }
                catch (_: OverlappingFileLockException) { null } ?: return null
                return CorpusFileLock(canonical, opened, lock).also { acquired = true }
            } finally {
                if (!acquired) try { channel?.close() } finally { openPaths.remove(canonical) }
            }
        }
    }
}
